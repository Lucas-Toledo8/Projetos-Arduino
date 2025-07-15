# core/arduino.py

import serial
import time
import threading
import os
import queue
import struct  # <<-- Importar struct para trabalhar com os pacotes binários

# Variável global para reter o caminho do arquivo selecionado.
selectedFilePathGlobalHack = ""

# --- DEFINIÇÕES DO PROTOCOLO (DEVE SER IDÊNTICO AO ARDUINO) ---
PACKET_TYPE_DATA = 0x01
PACKET_TYPE_ACK = 0x02
PACKET_TYPE_NACK = 0x03

# IDs de Mensagem Específicos para Pacotes de Status (usados com PACKET_TYPE_DATA)
MESSAGE_ID_COMBINED_STATUS = 252 # ID para o pacote de status combinado

# ID Único para ESTE lado do Python/Arduino
# IMPORTANTE: Use 0x01 para o primeiro conjunto (PC A + Arduino A)
#             Use 0x02 para o segundo conjunto (PC B + Arduino B)
# CERTIFIQUE-SE DE QUE ESTE ID CORRESPONDE AO THIS_DEVICE_ID NO SEU ARDUINO.INO
THIS_DEVICE_ID = 0x01 # <--- ATENÇÃO: ALTERE ESTE VALOR PARA 0x02 NO SEGUNDO SISTEMA

# PACKET_FIXED_OVERHEAD:
# packet_type (1B), device_id (1B), message_id (1B), fragment_idx (1B), total_fragments (2B), payload_len (1B) = 7 bytes
PACKET_FIXED_OVERHEAD = 7

# Tamanho máximo do payload que podemos colocar em nosso Packet: VW_MAX_PAYLOAD (27) - PACKET_FIXED_OVERHEAD (7) - crc_value (1) = 19 bytes
MAX_PACKET_PAYLOAD_SIZE = (27 - PACKET_FIXED_OVERHEAD - 1)
TOTAL_PACKET_SIZE = 27  # Tamanho total da struct Packet em bytes

# Formato de empacotamento/desempacotamento para a struct Packet
# <   : little-endian
# B   : unsigned char (packet_type)
# B   : unsigned char (device_id)
# B   : unsigned char (message_id)
# B   : unsigned char (fragment_idx)
# H   : unsigned short (total_fragments)
# B   : unsigned char (payload_len)
# 19s : 19 bytes string (para o payload_data)
# B   : unsigned char (crc_value)
PACKET_FORMAT = "<BBBBHB{}sB".format(MAX_PACKET_PAYLOAD_SIZE)

# --- Constantes para ARQ (DEVE SER IDÊNTICO AO ARDUINO) ---
RETRANSMISSION_TIMEOUT = 0.7  # Em segundos, deve corresponder ao Arduino (700ms)
MAX_RETRANSMISSION_ATTEMPTS = 5 # Deve corresponder ao Arduino
MAX_UNACKED_FRAGMENTS = 4     # Máximo de fragmentos não reconhecidos que podemos ter no buffer ARQ

# --- Variáveis para controle de sincronização TDMA no Python (DEVE SER IDÊNTICO AO ARDUINO) ---
TRANSMISSION_SLOT_DURATION_MS = 5000  # Em milissegundos
CYCLE_DURATION_MS = TRANSMISSION_SLOT_DURATION_MS * 2
THIS_DEVICE_ARDUINO_ID = THIS_DEVICE_ID # Usar o mesmo ID definido acima

# ===================================================================================

# CRC-4 (G(x) = x^4 + x + 1) - Tabela Lookup para Python
# IMPORTANTE: Tabela deve ser idêntica à do Arduino
CRC4_TABLE = (
    0x0, 0x3, 0x6, 0x5, 0xC, 0xF, 0xA, 0x9, 0xB, 0x8, 0xD, 0xE, 0x7, 0x4, 0x1, 0x2
)

def calculate_crc4(data_bytes):
    crc = 0x00
    for byte in data_bytes:
        # Processa os 4 bits mais significativos
        crc = CRC4_TABLE[crc ^ (byte >> 4)]
        # Processa os 4 bits menos significativos
        crc = CRC4_TABLE[crc ^ (byte & 0x0F)]
    return crc

class ArduinoController:
    def __init__(self, serial_port, baud_rate, log_callback=None, update_status_callback=None):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.serial_connection = None
        self.running = False
        self.read_thread = None
        self.log_callback = log_callback if log_callback else print
        self.update_status_callback = update_status_callback if update_status_callback else (lambda e, r: None) # Callback dummy
        self._is_connected_to_arduino_logic = False
        self._last_arduino_communication_time = 0.0
        self._is_sending_file_flag = False
        self._is_receiving_file_flag = False
        


        
        self.ack_queue = queue.Queue() # Fila para ACKs recebidos
        self.nack_queue = queue.Queue() # Fila para NACKs recebidos
        self.received_fragments = {} # {message_id: {fragment_idx: payload_data}}
        self.expected_total_fragments = {} # {message_id: total_fragments}
        self.received_message_ids = set() # Para rastrear Message IDs já recebidos e "completos"
        self._is_sending_file_flag = False # Flag para indicar se o envio de arquivo está ativo

        # Variáveis de estado do Arduino reportadas
        self.arduino_emitter_state = None
        self.arduino_receiver_state = None

        # Variáveis para o controle de turno TDMA no Python
        self.current_cycle_start_time = time.time() # Usa time.time() para Python
        self.current_transmitter_id = 0x01 # Começa sempre com o 0x01 transmitindo (exemplo)


    def _default_log_callback(self, message):
            print(f"[ArduinoController] {message}")

    def is_serial_port_open(self):
        """Verifica se a conexão serial Python está ativa."""
        return self.serial_connection is not None and self.serial_connection.is_open


    def connect(self):
        try:
            self.serial_connection = serial.Serial(self.serial_port, self.baud_rate, timeout=0.1)
            self.running = True
            self.read_thread = threading.Thread(target=self._serial_read_thread)
            self.read_thread.start()
            self.log_callback(f"Conectado à porta serial {self.serial_port} com {self.baud_rate} bps.")
            return True
        except serial.SerialException as e:
            self.log_callback(f"Erro ao conectar à porta serial {self.serial_port}: {e}")
            return False

    def disconnect(self):
        self.running = False
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join()
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            self.log_callback("Desconectado da porta serial.")


    def get_overall_arduino_status(self):
        return {
            "serial_connected": self.serial_connection and self.serial_connection.is_open,
            "arduino_active": self._is_connected_to_arduino_logic,
            "last_communication_secs": round(time.time() - self._last_arduino_communication_time, 2),
            "emitter_status": self.get_emitter_status(),
            "receiver_status": self.get_receiver_status(),
            "sending_flag": self._is_sending_file_flag,
            "receiving_flag": self._is_receiving_file_flag
        }

    EMITTER_STATE_MAP = {
        "0": "Desconectado",
        "1": "Conectado/Ocioso",
        "2": "Enviando Dados",
        "3": "Erro Comunicação",
        "4": "Erro Transmissão",
        "5": "Aguardando ACK",
        "6": "Enviado Completo"
    }
    RECEIVER_STATE_MAP = {
        "0": "Desconectado",
        "1": "Aguardando",
        "2": "Recebendo Dados",
        "3": "Erro Comunicação",
        "4": "Sinal Perdido",
        "5": "Sinal Fraco",
        "6": "Recebido Completo"
    }

    def get_emitter_status(self):
        val = str(self.arduino_emitter_state) if self.arduino_emitter_state is not None else "Desconhecido"
        return self.EMITTER_STATE_MAP.get(val, val)

    def get_receiver_status(self):
        val = str(self.arduino_receiver_state) if self.arduino_receiver_state is not None else "Desconhecido"
        return self.RECEIVER_STATE_MAP.get(val, val)

    def get_arduino_connection_status(self):
        """Retorna True se a conexão serial está aberta."""
        return self.is_serial_port_open()

    def test_serial_port_availability(self):
        """
        Tenta abrir e fechar a porta serial para verificar sua disponibilidade (hardware).
        Retorna True se puder abrir e fechar, False caso contrário.
        """
        self.log_callback(f"Verificando disponibilidade da porta serial {self.serial_port}...")
        try:
            temp_ser = serial.Serial(self.serial_port, self.baud_rate, timeout=1)  # Timeout menor para teste rápido
            temp_ser.close()
            self.log_callback(f"Porta serial {self.serial_port} está disponível.")
            return True
        except serial.SerialException as e:
            self.log_callback(f"Porta serial {self.serial_port} NÃO está disponível: {e}")
            return False
        except Exception as e:
            self.log_callback(f"Erro inesperado ao testar porta serial {self.serial_port}: {e}")
            return False

    # ### MODIFICADO: Lógica de leitura e RECEPTOR no Python
    def _read_from_arduino(self):
        thread_name = threading.current_thread().name
        self.log_callback(f"Iniciando thread de leitura serial: {thread_name}")
        while True:
            if self.ser and self.ser.is_open:
                try:
                    # Tenta ler o tamanho exato de um Packet
                    if self.ser.in_waiting >= TOTAL_PACKET_SIZE:
                        received_bytes = self.ser.read(TOTAL_PACKET_SIZE)

                        # Qualquer comunicação recebida indica que o Arduino está ativo
                        self._last_arduino_communication_time = time.time()
                        self._is_connected_to_arduino_logic = True

                        # Desempacota o pacote
                        try:
                            # packet_type, message_id, fragment_idx, total_fragments, payload_len, payload_data_bytes, crc_value
                            (
                                packet_type,
                                message_id,
                                fragment_idx,
                                total_fragments,
                                payload_len,
                                payload_data_bytes,
                                crc_value
                            ) = struct.unpack(PACKET_FORMAT, received_bytes)

                            # Valida o CRC-4
                            # Os dados para CRC são os mesmos que no Arduino, exceto o próprio crc_value
                            # packet_type, message_id, fragment_idx, total_fragments, payload_len, payload_data

                            # Cuidado aqui: A lógica do CRC no Arduino é sensível ao tipo de pacote.
                            # Para DATA, o CRC inclui todos os campos fixos + payload.
                            # Para ACK/NACK, o CRC inclui apenas type, message_id, fragment_idx.

                            calculated_crc = 0  # Inicializa

                            if packet_type == PACKET_TYPE_DATA:
                                # Prepara os bytes para calcular o CRC no Python
                                data_for_crc = struct.pack("<BBBBB",
                                                            packet_type, message_id, fragment_idx, total_fragments,
                                                            payload_len
                                                            ) + payload_data_bytes[:payload_len]  # Apenas o payload real

                                calculated_crc = calculate_crc4(data_for_crc)

                                self.log_callback(
                                    f"RF_Received: Type=0x{packet_type:02x}, MsgID={message_id}, Frag={fragment_idx}/{total_fragments}, "
                                    f"P-Len={payload_len}, CRC_Recv=0x{crc_value:02x}, CRC_Calc=0x{calculated_crc:02x}"
                                )

                                if calculated_crc == crc_value:
                                    self.log_callback("CRC OK!")
                                    # Processa o pacote DATA
                                    self._process_received_data_packet(message_id, fragment_idx, total_fragments,
                                                                        payload_data_bytes[:payload_len])
                                else:
                                    self.log_callback(
                                        f"CRC ERROR on DATA packet! MsgID={message_id}, Frag={fragment_idx}. Discarding.")
                                    # Em um sistema ARQ, o Arduino já enviaria um NACK se o CRC RF estivesse errado.
                                    # Aqui, apenas descartamos o fragmento corrompido.

                            elif packet_type == PACKET_TYPE_ACK or packet_type == PACKET_TYPE_NACK:
                                # Para ACK/NACK, o CRC é calculado apenas sobre type, message_id, fragment_idx
                                data_for_crc = struct.pack("<BBB", packet_type, message_id, fragment_idx)
                                calculated_crc = calculate_crc4(data_for_crc)

                                self.log_callback(
                                    f"RF_Received: Type=0x{packet_type:02x}, MsgID={message_id}, Frag={fragment_idx}, "
                                    f"CRC_Recv=0x{crc_value:02x}, CRC_Calc=0x{calculated_crc:02x}"
                                )

                                if calculated_crc == crc_value:
                                    if packet_type == PACKET_TYPE_ACK:
                                        self.log_callback(
                                            f"ACK for MsgID={message_id}, Frag={fragment_idx} received. (Python emitter side should handle this)")
                                        # Implementar lógica de ARQ para o EMISSOR Python aqui, se necessário.
                                        # No momento, o Arduino já cuida do ARQ para o RF.
                                        # Podemos adicionar um mecanismo para que o Python saiba que um pacote foi ACKed.
                                    elif packet_type == PACKET_TYPE_NACK:
                                        self.log_callback(
                                            f"NACK for MsgID={message_id}, Frag={fragment_idx} received. (Python emitter side should handle this)")
                                        # Idem ao ACK.
                                else:
                                    self.log_callback(
                                        f"CRC ERROR on ACK/NACK packet! Type=0x{packet_type:02x}, MsgID={message_id}, Frag={fragment_idx}. Discarding.")

                        except struct.error as se:
                            self.log_callback(f"Erro ao desempacotar pacote serial (struct error): {se}. Bytes: {received_bytes.hex()}")
                        except Exception as e:
                            self.log_callback(f"Erro inesperado ao processar pacote serial: {e}. Bytes: {received_bytes.hex()}")
                    elif self.ser.in_waiting > 0:
                        # Se há bytes, mas não o suficiente para um Packet completo, pode ser uma mensagem de debug.
                        # Tentar ler como linha para logs, mas priorizar Packet.
                        # Isso pode ser problemático se mensagens de debug se misturarem com pacotes.
                        # O ideal é que o Arduino SÓ mande structs Packet.
                        try:
                            # read_until('\n') é mais robusto que readline() para garantir que pegue a linha completa
                            line_bytes = self.ser.read_until(b'\n')
                            line = line_bytes.decode('utf-8', errors='ignore').strip()
                            if line:
                                self.log_callback(f"Arduino (Debug/Incomplete Packet?): {line}")
                                # Atualiza o timestamp mesmo para mensagens de debug para manter a conexão lógica
                                self._last_arduino_communication_time = time.time()
                                self._is_connected_to_arduino_logic = True
                        except Exception as e:
                            self.log_callback(f"Erro ao ler linha de debug: {e}")

                except serial.SerialException as e:
                    self.log_callback(f"Erro na leitura serial: {e}. Desconectado.")
                    self._close_serial()
                    self._is_receiving_file_flag = False
                    break
                except Exception as e:
                    self.log_callback(f"Erro inesperado na leitura serial: {e}")
                    self._close_serial()
                    self._is_receiving_file_flag = False
                    break
            time.sleep(0.05)  # Pequeno atraso para evitar busy-waiting



    def _serial_read_thread(self):
        buffer = b''
        while self.running:
            try:
                if self.serial_connection.in_waiting > 0:
                    data = self.serial_connection.read(self.serial_connection.in_waiting)
                    buffer += data

                    while len(buffer) >= TOTAL_PACKET_SIZE:
                        raw_packet_bytes = buffer[:TOTAL_PACKET_SIZE]
                        buffer = buffer[TOTAL_PACKET_SIZE:] # Remove o pacote lido do buffer

                        # Desempacota os bytes para obter os campos do pacote
                        (packet_type, device_id, message_id, fragment_idx, total_fragments, payload_len, payload_data, crc_value) = \
                            struct.unpack(PACKET_FORMAT, raw_packet_bytes)

                        # Limpar bytes nulos extras no payload_data
                        payload_data = payload_data[:payload_len]

                        # Lógica de Verificação de CRC no lado do Python
                        calculated_crc = 0
                        crc_data = b'' # Inicializa como bytes vazios

                        # Monta os bytes para o cálculo do CRC (DEVE SER IDÊNTICO ao ARDUINO)
                        # type (1), dev_id (1), msg_id (1), frag_idx (1), total_frags (2), payload_len (1) + payload_data
                        if packet_type == PACKET_TYPE_DATA:
                            crc_data = struct.pack("<BBBBHB",
                                                   packet_type,
                                                   device_id,
                                                   message_id,
                                                   fragment_idx,
                                                   total_fragments,
                                                   payload_len) + payload_data
                        # Para ACK/NACK packets: type (1), dev_id (1), msg_id (1), frag_idx (1)
                        elif packet_type in [PACKET_TYPE_ACK, PACKET_TYPE_NACK]:
                            crc_data = struct.pack("<BBBB",
                                                   packet_type,
                                                   device_id,
                                                   message_id,
                                                   fragment_idx)
                        else:
                            self.log_callback(f"AVISO: Pacote recebido com tipo desconhecido para CRC: 0x{packet_type:02X}")
                            continue # Pular pacote desconhecido

                        calculated_crc = calculate_crc4(crc_data)

                        if calculated_crc != crc_value:
                            # Removido o debug temporário para não poluir o código final
                            self.log_callback(f"ERRO: CRC INVALIDO para pacote (Tipo: 0x{packet_type:02X}, DevID: 0x{device_id:02X}, MsgID: {message_id}, Frag: {fragment_idx})! Recebido: 0x{crc_value:02X}, Calculado: 0x{calculated_crc:02X}")
                            # Envia um NACK de volta para o Arduino se for um pacote de dados inválido e não for um pacote de status
                            if packet_type == PACKET_TYPE_DATA and message_id != MESSAGE_ID_COMBINED_STATUS:
                                self.send_nack(message_id, fragment_idx) # Envia NACK para o Arduino
                            continue # Pula o processamento do pacote inválido
                        
                        # Filtra pacotes do próprio ID para evitar loopbacks
                        if device_id == THIS_DEVICE_ID:
                            # self.log_callback(f"DEBUG: Ignorando pacote recebido do próprio dispositivo ID: 0x{device_id:02X}")
                            continue # Ignorar pacotes originados pelo nosso próprio sistema


                        self.log_callback(f"Pacote RF recebido -> Tipo: 0x{packet_type:02X}, DevID: 0x{device_id:02X}, MsgID: {message_id}, Frag: {fragment_idx}/{total_fragments}, P-Len: {payload_len}, CRC: 0x{crc_value:02X}")

                        # Processamento de Pacotes de Status Combinados
                        if packet_type == PACKET_TYPE_DATA and message_id == MESSAGE_ID_COMBINED_STATUS:
                            if payload_len >= 2:
                                self.arduino_emitter_state = payload_data[0]
                                self.arduino_receiver_state = payload_data[1]

                                # Se houver terceiro byte, atualiza o ARQ também
                                if payload_len >= 3:
                                    self.arduino_buffer_arq_count = payload_data[2]
                                else:
                                    self.arduino_buffer_arq_count = 0  # Valor padrão se não enviado

                                self.update_status_callback(self.arduino_emitter_state, self.arduino_receiver_state)
                            else:
                                self.log_callback("AVISO: Pacote de status combinado com payload_len muito curto.")

                            continue # Pacote de status processado, nada mais a fazer para ele

                        # Processamento normal de pacotes ACK/NACK/DATA
                        if packet_type == PACKET_TYPE_ACK:
                            self.ack_queue.put({'message_id': message_id, 'fragment_idx': fragment_idx})
                            self.log_callback(f"ACK recebido para MsgID: {message_id}, Frag: {fragment_idx}")
                        elif packet_type == PACKET_TYPE_NACK:
                            self.nack_queue.put({'message_id': message_id, 'fragment_idx': fragment_idx})
                            self.log_callback(f"NACK recebido para MsgID: {message_id}, Frag: {fragment_idx}")
                        elif packet_type == PACKET_TYPE_DATA:
                            if message_id in self.received_message_ids:
                                # self.log_callback(f"DEBUG: Fragmento de MsgID já concluída {message_id}, ignorando.")
                                self.send_ack(message_id, fragment_idx) # Re-envia ACK para garantir
                                continue

                            if message_id not in self.received_fragments:
                                self.received_fragments[message_id] = {}
                                self.expected_total_fragments[message_id] = total_fragments

                            self.received_fragments[message_id][fragment_idx] = payload_data

                            # Verifica se todos os fragmentos foram recebidos
                            if len(self.received_fragments[message_id]) == self.expected_total_fragments[message_id]:
                                # Reconstruir o arquivo
                                full_file_data = b''
                                for i in range(self.expected_total_fragments[message_id]):
                                    if i in self.received_fragments[message_id]:
                                        full_file_data += self.received_fragments[message_id][i]
                                    else:
                                        self.log_callback(f"ERRO: Fragmento {i} faltando para MsgID {message_id}!")
                                        break # Sai do loop se um fragmento estiver faltando

                                # Se todos os fragmentos foram coletados com sucesso
                                if len(self.received_fragments[message_id]) == self.expected_total_fragments[message_id]:
                                    self.log_callback(f"ARQUIVO COMPLETO RECEBIDO (MsgID: {message_id})! Tamanho: {len(full_file_data)} bytes.")
                                    
                                    # Gera um nome de arquivo único
                                    output_filename = f"received_file_msgid_{message_id}_{int(time.time())}.txt"
                                    script_dir = os.path.dirname(os.path.abspath(__file__))
                                    output_filepath = os.path.join(script_dir, output_filename)

                                    try:
                                        with open(output_filepath, "wb") as f:
                                            f.write(full_file_data)
                                        self.log_callback(f"Arquivo salvo em: {output_filepath}")
                                    except Exception as file_e:
                                        self.log_callback(f"ERRO ao salvar arquivo: {file_e}")

                                    # Limpa o estado para esta mensagem
                                    del self.received_fragments[message_id]
                                    del self.expected_total_fragments[message_id]
                                    self.received_message_ids.add(message_id) # Marca como mensagem completa

                                    # Envia ACK para o último fragmento também para confirmar o recebimento completo
                                    self.send_ack(message_id, fragment_idx)
                                else:
                                    self.log_callback(f"AVISO: Fragmentos faltando para MsgID {message_id}, aguardando retransmissao.")
                                    # Não envia ACK se houver fragmentos faltando, espera por eles ou NACK do outro lado
                                    self.send_nack(message_id, fragment_idx) # Opcional: envia NACK para o último fragmento recebido se algo estiver faltando

                            else:
                                self.log_callback(f"Fragmento {fragment_idx} de {total_fragments} para MsgID {message_id} recebido.")
                                # Envia ACK para o fragmento individual recebido
                                self.send_ack(message_id, fragment_idx)


            except serial.SerialException as e:
                self.log_callback(f"Erro serial: {e}")
                break
            except struct.error as e:
                self.log_callback(f"Erro de desempacotamento (struct): {e}. Buffer: {raw_packet_bytes}")
                # Pode haver um pacote incompleto ou corrompido no buffer, tentar limpar
                buffer = b'' # Limpar buffer para tentar se recuperar
            except Exception as e:
                self.log_callback(f"Erro inesperado na thread de leitura serial: {e}")
                # Opcional: Limpar buffer ou tentar se recuperar
                buffer = b''
            time.sleep(0.001) # Pequeno atraso para não sobrecarregar a CPU

    def _send_packet_to_arduino(self, packet_type, message_id, fragment_idx, total_fragments, payload_data):
        payload_len = len(payload_data)

        if payload_len > MAX_PACKET_PAYLOAD_SIZE:
            self.log_callback(f"ERRO: Payload excede o tamanho máximo permitido ({MAX_PACKET_PAYLOAD_SIZE} bytes).")
            return {"status": "error", "message": "Payload muito grande."}
        
        # Preenche o payload com bytes nulos se for menor que MAX_PACKET_PAYLOAD_SIZE
        # Isso garante que a struct.pack sempre tenha o tamanho esperado
        padded_payload_data = payload_data + b'\0' * (MAX_PACKET_PAYLOAD_SIZE - payload_len)

        # Monta os bytes para o cálculo do CRC
        # ATENÇÃO: A ordem e o número de bytes DEVE ser idêntico ao que o Arduino usa para CRC
        # packet_type, device_id, message_id, fragment_idx, total_fragments (2 bytes), payload_len, payload_data
        
        # Para DATA packets: type (1), dev_id (1), msg_id (1), frag_idx (1), total_frags (2), payload_len (1) + payload_data
        if packet_type == PACKET_TYPE_DATA:
            crc_data = struct.pack("<BBBBHB",
                                   packet_type,
                                   THIS_DEVICE_ID,
                                   message_id,
                                   fragment_idx,
                                   total_fragments,
                                   payload_len) + padded_payload_data
        # Para ACK/NACK packets: type (1), dev_id (1), msg_id (1), frag_idx (1)
        elif packet_type in [PACKET_TYPE_ACK, PACKET_TYPE_NACK]:
            crc_data = struct.pack("<BBBB",
                                   packet_type,
                                   THIS_DEVICE_ID,
                                   message_id,
                                   fragment_idx)
        else:
            self.log_callback(f"ERRO: Tipo de pacote desconhecido para CRC: 0x{packet_type:02X}")
            return {"status": "error", "message": "Tipo de pacote desconhecido."}

        crc_value = calculate_crc4(crc_data)

        # Monta o pacote binário final
        # Formato: <BBBBHB{}sB
        #          packet_type, device_id, message_id, fragment_idx, total_fragments, payload_len, payload_data, crc_value
        full_packet_bytes = struct.pack(PACKET_FORMAT,
                                        packet_type,
                                        THIS_DEVICE_ID,
                                        message_id,
                                        fragment_idx,
                                        total_fragments,
                                        payload_len,
                                        padded_payload_data,
                                        crc_value)

        try:
            self.serial_connection.write(full_packet_bytes)
            # self.log_callback(f"SERIAL -> Pacote enviado (Tipo: 0x{packet_type:02X}, MsgID: {message_id}, Frag: {fragment_idx}, CRC: 0x{crc_value:02X})")
            return {"status": "success", "message": "Pacote enviado."}
        except Exception as e:
            self.log_callback(f"ERRO ao enviar pacote serial: {e}")
            return {"status": "error", "message": str(e)}

    def send_data_packet(self, message_id, fragment_idx, total_fragments, payload_data):
        return self._send_packet_to_arduino(PACKET_TYPE_DATA, message_id, fragment_idx, total_fragments, payload_data)

    def send_ack(self, message_id, fragment_idx):
        # ACK/NACK não precisam de total_fragments ou payload_data
        return self._send_packet_to_arduino(PACKET_TYPE_ACK, message_id, fragment_idx, 0, b'')

    def send_nack(self, message_id, fragment_idx):
        return self._send_packet_to_arduino(PACKET_TYPE_NACK, message_id, fragment_idx, 0, b'')

    def is_sending_file(self):
        return self._is_sending_file_flag

    def get_arduino_states(self):
        # Retorna o último estado de emissor e receptor reportado pelo Arduino
        return self.arduino_emitter_state, self.arduino_receiver_state
    
    # Método para verificar se é o turno de transmissão deste dispositivo
    def is_my_turn_to_transmit(self):
        current_time_ms = int(time.time() * 1000) # Converte para ms para compatibilidade com Arduino
        
        # Calcula o tempo dentro do ciclo atual
        time_in_cycle = (current_time_ms - int(self.current_cycle_start_time * 1000)) % CYCLE_DURATION_MS

        # Determina o ID do transmissor atual com base no tempo no ciclo
        # (DEVE CORRESPONDER À LÓGICA DO ARDUINO)
        if time_in_cycle < TRANSMISSION_SLOT_DURATION_MS:
            transmitter_in_slot = 0x01
        else:
            transmitter_in_slot = 0x02

        # Atualiza o tempo de início do ciclo se um novo ciclo começou
        # Para evitar que o tempo_in_cycle se torne muito grande
        if current_time_ms - int(self.current_cycle_start_time * 1000) >= CYCLE_DURATION_MS:
            self.current_cycle_start_time = time.time() # Reinicia o tempo para o próximo ciclo

        return transmitter_in_slot == THIS_DEVICE_ARDUINO_ID


    def send_file(self, file_path, cancel_flag, update_progress_callback=None, on_sending_finished_callback=None, update_frames_summary_callback=None):
        self._is_sending_file_flag = True
        final_status = 'success'
        final_message = 'Envio de arquivo concluído.'
        num_segments = 0
        total_bytes_sent_original = 0 # Conta apenas os bytes de dados originais, não o padding

        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            total_file_size = len(file_bytes)
            
            message_id = int(time.time() % 256) # ID único para esta mensagem

            # Divide o arquivo em segmentos
            segments = [file_bytes[i:i + MAX_PACKET_PAYLOAD_SIZE]
                        for i in range(0, total_file_size, MAX_PACKET_PAYLOAD_SIZE)]
            total_fragments = len(segments)

            self.log_callback(f"Iniciando envio do arquivo '{os.path.basename(file_path)}' com {total_fragments} fragmentos. MsgID: {message_id}")

            for i, segment_bytes in enumerate(segments):
                if cancel_flag.is_set():
                    self.log_callback("Envio de arquivo cancelado pelo usuário.")
                    final_status = 'cancelled'
                    final_message = 'Envio cancelado.'
                    break

                # VERIFICA SE O ARDUINO TEM ESPAÇO NO BUFFER ARQ
                while self.arduino_buffer_arq_count >= MAX_UNACKED_FRAGMENTS:
                    self.log_callback("Esperando o Arduino liberar espaço no buffer ARQ...")
                    time.sleep(0.05)

                # Aguarda pelo turno de transmissão deste dispositivo
                while not self.is_my_turn_to_transmit():
                    # self.log_callback("AGUARDANDO MEU TURNO DE TRANSMISSÃO...") # Debug opcional
                    time.sleep(0.01) # Espera um pouco antes de verificar novamente

                # Tenta enviar o pacote com ARQ
                retransmission_attempts = 0
                ack_received = False
                nack_received = False

                while retransmission_attempts < MAX_RETRANSMISSION_ATTEMPTS:
                    result = self.send_data_packet(message_id, i, total_fragments, segment_bytes)
                    if result["status"] == "error":
                        self.log_callback(f"Erro ao enviar pacote para o Arduino: {result['message']}")
                        time.sleep(0.1) # Pequena pausa em caso de erro de envio para a serial
                        continue # Tenta novamente (conta como uma retransmissão)

                    # Aguarda por ACK ou NACK
                    start_wait_time = time.time()
                    while time.time() - start_wait_time < RETRANSMISSION_TIMEOUT:
                        try:
                            # Tenta pegar um ACK
                            ack = self.ack_queue.get(timeout=0.01) # Curto timeout para não bloquear
                            if ack['message_id'] == message_id and ack['fragment_idx'] == i:
                                ack_received = True
                                break
                        except queue.Empty:
                            pass # Nenhuma ACK, continua esperando ou verifica NACK

                        try:
                            # Tenta pegar um NACK
                            nack = self.nack_queue.get(timeout=0.01)
                            if nack['message_id'] == message_id and nack['fragment_idx'] == i:
                                nack_received = True
                                break
                        except queue.Empty:
                            pass # Nenhuma NACK
                        
                        # Durante a espera por ACK/NACK, verificar se ainda é nosso turno.
                        # Se não for mais, e o ACK/NACK não veio, pode significar que o outro lado não teve slot para responder.
                        # Isso é uma simplificação para o TDMA básico. Em sistemas reais, ACK/NACK podem ter slots dedicados.
                        if not self.is_my_turn_to_transmit():
                            self.log_callback("AVISO: Turno de TX expirou esperando ACK/NACK. Retransmitindo no proximo turno.")
                            break # Sai da espera, força retransmissão no próximo ciclo

                    if ack_received:
                        break # Sai do loop de retransmissão, vai para o próximo segmento
                    elif nack_received:
                        self.log_callback(f"NACK recebido para MsgID: {message_id}, Frag: {i}. Retransmitindo.")
                        retransmission_attempts += 1
                        ack_received = False # Reseta para próxima tentativa
                        nack_received = False # Reseta para próxima tentativa
                    else: # Timeout
                        self.log_callback(f"Timeout para MsgID: {message_id}, Frag: {i}. Tentativa {retransmission_attempts + 1}/{MAX_RETRANSMISSION_ATTEMPTS}.")
                        retransmission_attempts += 1
                
                if not ack_received:
                    self.log_callback(f"ERRO: Max. tentativas de retransmissao atingidas para MsgID: {message_id}, Frag: {i}. ({result['message']})")
                    final_status = 'error'
                    final_message = f"Erro ao enviar segmento {i}: {result['message']}"
                    break

                total_bytes_sent_original += len(segment_bytes)
                num_segments += 1

                if update_progress_callback:
                    percentage = int((total_bytes_sent_original / total_file_size) * 100)
                    update_progress_callback(percentage)

                if update_frames_summary_callback:
                    update_frames_summary_callback(num_segments, total_bytes_sent_original)

                time.sleep(0.05)  # Pequeno atraso entre envios de pacotes para o Arduino processar

            if not cancel_flag.is_set() and final_status == 'success':
                self.log_callback(
                    f"Envio de arquivo concluído: {num_segments} segmentos ({total_bytes_sent_original} bytes de dados originais).")
                if update_progress_callback:
                    update_progress_callback(100)

        except Exception as e:
            self.log_callback(f"Erro inesperado durante o envio do arquivo: {e}")
            final_status = 'error'
            final_message = f'Erro inesperado durante o envio: {e}'
        finally:
            self._is_sending_file_flag = False  # Finaliza o estado de envio
            if on_sending_finished_callback:
                on_sending_finished_callback(final_status, final_message)

    def get_serial_port_status(self):
        """Retorna 'Disponível' ou 'Indisponível' para o frontend."""
        available = self.test_serial_port_availability()
        return "Disponível" if available else "Indisponível"

    def get_connectivity_status(self):
        return {
            "computer": "OK",
            "serial_port": "Disponível" if self.test_serial_port_availability() else "Indisponível",
            "arduino": "Conectado" if self.is_serial_port_open() else "Desconectado",
            "rf_emitter": self.get_emitter_status(),
            "rf_receiver": self.get_receiver_status(),
        }
