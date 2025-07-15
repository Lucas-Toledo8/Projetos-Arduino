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


# --- FIM DAS DEFINIÇÕES DO PROTOCOLO ---

class ArduinoController:
    def __init__(self, serial_port, baud_rate, log_callback=None, on_file_received_callback=None):
        # A propriedade max_payload_size foi simplificada, agora apenas a RF max payload.
        # Não precisamos mais de 'max_data_segment_size_python' separada
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        # self.max_payload_size_arduino_rf = max_payload_size # Esta variável não é mais necessária como parâmetro de init, pois é uma constante do protocolo
        # self.max_data_segment_size_python = self.max_payload_size_arduino_rf - 1 # Esta também não é mais necessária

        self.ser = None
        self.serial_timeout = 1
        self.log_callback = log_callback if log_callback else self._default_log_callback

        self._sending_thread = None

        # Variáveis para o RECEPTOR no Python
        # self._receiving_from_arduino = False # Será gerenciado pelos metadados do pacote
        self._incoming_file_buffer = {}  # Dicionário para armazenar fragmentos por message_id e fragment_idx
        # _incoming_file_name será extraído do primeiro pacote de dados (header)
        self._expected_total_fragments = {}  # Armazena o total_fragments esperado por message_id
        self._on_file_received_callback = on_file_received_callback

        self.received_files_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'received_files')
        os.makedirs(self.received_files_dir, exist_ok=True)


        # === NOVAS/MODIFICADAS: Variáveis para INFERIR o estado de conectividade ===
        self._last_arduino_communication_time = 0.0  # Timestamp da última mensagem recebida do Arduino
        self._is_connected_to_arduino_logic = False  # Status lógico baseado na comunicação
        self._is_sending_file_flag = False  # Flag para saber se um envio de arquivo está em andamento (Emissor)
        self._is_receiving_file_flag = False  # Flag para saber se um recebimento de arquivo está em andamento (Receptor)

        self.connect_arduino()  # Já tenta conectar na inicialização

    def _default_log_callback(self, message):
        print(f"[ArduinoController] {message}")

    def is_serial_port_open(self):
        """Verifica se a conexão serial Python está ativa."""
        return self.ser is not None and self.ser.is_open

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

    def connect_arduino(self):
        """Tenta conectar ao Arduino. Retorna True em sucesso, False em falha."""
        if self.ser and self.ser.is_open:
            self.log_callback(f"Já conectado à serial em {self.serial_port}.")
            return True

        try:
            self.ser = serial.Serial(self.serial_port, self.baud_rate, timeout=self.serial_timeout)
            time.sleep(2)  # Espera o Arduino reiniciar ou inicializar
            self.log_callback(f"Conectado à serial em {self.serial_port}")

            # Inicia a thread de leitura serial APENAS se não estiver rodando
            if not any(isinstance(t, threading.Thread) and t.name == "SerialReaderThread" for t in threading.enumerate()):
                threading.Thread(target=self._read_from_arduino, daemon=True, name="SerialReaderThread").start()

            # Atualiza o status lógico do Arduino ao conectar
            self._is_connected_to_arduino_logic = True
            self._last_arduino_communication_time = time.time()  # Reseta o tempo de comunicação

            return True
        except serial.SerialException as e:
            self.log_callback(f"Erro ao conectar à serial: {e}")
            self.ser = None
            self._is_connected_to_arduino_logic = False  # Desconecta logicamente
            return False
        except Exception as e:
            self.log_callback(f"Erro inesperado ao conectar à serial: {e}")
            self.ser = None
            self._is_connected_to_arduino_logic = False  # Desconecta logicamente
            return False

    # === Métodos para obter o status para a API principal ===
    def get_arduino_connection_status(self):
        """Retorna o status lógico da conexão com o Arduino."""
        # Se a porta serial está aberta E se houve comunicação recente (dentro de 5 segundos)
        if self.is_serial_port_open() and (time.time() - self._last_arduino_communication_time < 5):
            return "Conectado"
        elif self.is_serial_port_open():
            return "Inativo/Sem Resposta"  # Porta aberta, mas Arduino não se comunica há algum tempo
        else:
            return "Desconectado"

    def get_emitter_status(self):
        """Retorna o status do módulo Emissor (do ponto de vista do Python)."""
        if not self.is_serial_port_open():
            return "Serial Desconectada"
        if not self._is_connected_to_arduino_logic:
            return "Arduino Inativo"  # Arduino não está se comunicando
        if self._is_sending_file_flag:
            return "Enviando"
        return "Pronto"

    def get_receiver_status(self):
        """Retorna o status do módulo Receptor (do ponto de vista do Python)."""
        if not self.is_serial_port_open():
            return "Serial Desconectada"
        if not self._is_connected_to_arduino_logic:
            return "Arduino Inativo"  # Arduino não está se comunicando
        if self._is_receiving_file_flag:
            return "Recebendo"
        return "Pronto"

    # Início da nova função
    def get_overall_arduino_status(self):
        """
        Retorna um dicionário com o status geral da conexão e dos módulos Emissor/Receptor.
        """
        return {
            "connection_status": self.get_arduino_connection_status(),
            "emitter_status": self.get_emitter_status(),
            "receiver_status": self.get_receiver_status()
        }
    # Fim da nova função

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

    def _process_received_data_packet(self, message_id, fragment_idx, total_fragments, payload_data):
        self._is_receiving_file_flag = True  # Indica que estamos recebendo um arquivo

        if message_id not in self._incoming_file_buffer:
            self.log_callback(f"Iniciando recepção de nova mensagem/arquivo (ID: {message_id}, Total Frags: {total_fragments}).")
            self._incoming_file_buffer[message_id] = [None] * total_fragments
            self._expected_total_fragments[message_id] = total_fragments
            # Assume que o nome do arquivo virá no primeiro fragmento (payload_data), se aplicável
            # Isso pode ser um problema se o nome for muito longo. Uma abordagem melhor seria um pacote HEAD.
            # Por enquanto, vamos assumir que o primeiro fragmento DATA tem o nome do arquivo.
            # Ou, se o Arduino não envia nome, geramos um default.
            # Para o nosso protocolo, o Arduino não envia o nome do arquivo via DATA packet.
            # Então, vamos gerar um nome de arquivo temporário ou fixo.
            # Se o primeiro fragmento do arquivo não contiver o nome, teremos que defini-lo por padrão.

            # Vamos remover a lógica de 'START_FILE' do Arduino e trataremos o recebimento como puramente binário.
            # O nome do arquivo será 'received_file_{message_id}.bin' por padrão.
            self._incoming_file_name = f"received_file_{message_id}.bin"  # Default para um novo arquivo

        if fragment_idx < total_fragments:  # Verifica se o índice é válido
            if self._incoming_file_buffer[message_id][fragment_idx] is None:
                self._incoming_file_buffer[message_id][fragment_idx] = payload_data
                self.log_callback(f"MsgID {message_id}: Fragmento {fragment_idx}/{total_fragments} recebido. Payload len: {len(payload_data)}")
            else:
                self.log_callback(f"MsgID {message_id}: Fragmento {fragment_idx} JÁ RECEBIDO. Descartando duplicata.")
        else:
            self.log_callback(f"MsgID {message_id}: Fragmento {fragment_idx} inválido. Ignorando.")

        # Verifica se todos os fragmentos para esta message_id foram recebidos
        if all(f is not None for f in self._incoming_file_buffer[message_id]):
            self.log_callback(f"MsgID {message_id}: Todos os {total_fragments} fragmentos recebidos. Remontando arquivo.")
            full_file_content = b''.join(self._incoming_file_buffer[message_id])

            # Salva o arquivo
            output_path = os.path.join(self.received_files_dir, self._incoming_file_name)
            try:
                with open(output_path, 'wb') as f:
                    f.write(full_file_content)
                self.log_callback(f"Arquivo '{self._incoming_file_name}' (MsgID: {message_id}) salvo com sucesso em: {output_path}")
                if self._on_file_received_callback:
                    self._on_file_received_callback('success', self._incoming_file_name, f"Arquivo salvo em: {output_path}")
            except Exception as e:
                self.log_callback(f"Erro ao salvar arquivo recebido (MsgID: {message_id}): {e}")
                if self._on_file_received_callback:
                    self._on_file_received_callback('error', self._incoming_file_name, f"Erro ao salvar: {e}")
            finally:
                # Limpa os buffers para esta mensagem
                del self._incoming_file_buffer[message_id]
                del self._expected_total_fragments[message_id]
                self._is_receiving_file_flag = False  # Finaliza o recebimento de arquivo

    def _close_serial(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.log_callback("Conexão serial fechada.")
        self.ser = None
        self._is_connected_to_arduino_logic = False  # Reseta o status lógico do Arduino
        self._is_sending_file_flag = False
        self._is_receiving_file_flag = False
        self._last_arduino_communication_time = 0.0  # Zera o timestamp
        # Limpa todos os buffers de arquivos incompletos
        self._incoming_file_buffer = {}
        self._expected_total_fragments = {}

    # --- NOVO: Função para criar e enviar um pacote genérico ---
    def _send_packet(self, packet_type: int, message_id: int, fragment_idx: int, total_fragments: int,
                     payload_data: bytes) -> dict:
        if not self.ser or not self.ser.is_open:
            self.log_callback("Erro: Serial não conectada para enviar pacote.")
            return {"status": "error", "message": "Serial não conectada."}

        # Garante que o payload não exceda o tamanho máximo
        if len(payload_data) > MAX_PACKET_PAYLOAD_SIZE:
            self.log_callback(f"Erro: Payload excede o tamanho máximo permitido ({MAX_PACKET_PAYLOAD_SIZE} bytes). Truncando.")
            payload_data = payload_data[:MAX_PACKET_PAYLOAD_SIZE]

        payload_len = len(payload_data)

        # Preenche o payload com zeros se for menor que MAX_PACKET_PAYLOAD_SIZE
        padded_payload = payload_data + b'\0' * (MAX_PACKET_PAYLOAD_SIZE - payload_len)

        # Calcula o CRC4 para os campos relevantes do pacote
        # Os dados para CRC são os mesmos que no Arduino, exceto o próprio crc_value
        # packet_type, message_id, fragment_idx, total_fragments, payload_len, payload_data
        data_for_crc = struct.pack("<BBBBB",
                                    packet_type, message_id, fragment_idx, total_fragments, payload_len
                                    ) + payload_data  # Use o payload REAL para o cálculo do CRC (sem padding)

        crc_value = calculate_crc4(data_for_crc)

        # Empacota todos os campos no formato da struct Packet
        packed_packet = struct.pack(PACKET_FORMAT,
                                     packet_type,
                                     message_id,
                                     fragment_idx,
                                     total_fragments,
                                     payload_len,
                                     padded_payload,
                                     crc_value)

        try:
            self.ser.write(packed_packet)
            self.log_callback(
                f"Python->Arduino (Packed): Type=0x{packet_type:02x}, MsgID={message_id}, Frag={fragment_idx}/{total_fragments}, "
                f"P-Len={payload_len}, CRC=0x{crc_value:02x}. Total {len(packed_packet)} bytes."
            )
            self._last_arduino_communication_time = time.time()
            self._is_connected_to_arduino_logic = True
            return {"status": "success", "message": "Pacote enviado."}
        except Exception as e:
            self.log_callback(f"Erro ao enviar pacote para Arduino: {e}")
            self._is_connected_to_arduino_logic = False
            return {"status": "error", "message": f"Erro ao enviar: {e}"}

    def send_text_message(self, message):
        # A mensagem de texto agora será enviada como um pacote de DADOS normal
        # Usaremos message_id=0 e fragment_idx=0, total_fragments=1 para mensagens de texto simples
        message_bytes = message.encode('utf-8')
        # Limita o tamanho da mensagem ao MAX_PACKET_PAYLOAD_SIZE
        if len(message_bytes) > MAX_PACKET_PAYLOAD_SIZE:
            self.log_callback(f"Mensagem de texto muito longa. Truncando para {MAX_PACKET_PAYLOAD_SIZE} bytes.")
            message_bytes = message_bytes[:MAX_PACKET_PAYLOAD_SIZE]

        return self._send_packet(
            PACKET_TYPE_DATA,
            0,  # message_id fixo para mensagens de texto simples
            0,  # fragment_idx fixo
            1,  # total_fragments fixo (mensagem única)
            message_bytes
        )

    def send_file_content(self, file_path, update_progress_callback=None, update_card_status_callback=None,
                          update_frames_summary_callback=None, cancel_flag=None, on_sending_finished_callback=None):

        if not self.is_serial_port_open() or not self._is_connected_to_arduino_logic:
            self.log_callback("Erro: Serial não conectada ou Arduino inativo para iniciar o envio de arquivo.")
            if on_sending_finished_callback:
                on_sending_finished_callback('error', 'Arduino não conectado ou não respondendo para iniciar o envio.')
            return {"status": "error", "message": "Arduino não conectado ou não respondendo."}

        if self._sending_thread and self._sending_thread.is_alive():
            self.log_callback("Erro: Um envio já está em andamento. Cancele o envio atual primeiro.")
            if on_sending_finished_callback:
                on_sending_finished_callback('error', 'Um envio já está em andamento.')
            return {"status": "error", "message": "Um envio já está em andamento."}

        if not os.path.exists(file_path):
            self.log_callback("Caminho do arquivo inválido ou arquivo não encontrado.")
            if on_sending_finished_callback:
                on_sending_finished_callback('error', 'Caminho do arquivo inválido ou arquivo não encontrado.')
            return {"status": "error", "message": "Caminho do arquivo inválido ou arquivo não encontrado."}

        global selectedFilePathGlobalHack
        selectedFilePathGlobalHack = file_path

        try:
            with open(file_path, 'rb') as f:
                file_content_bytes = f.read()

            total_file_size = len(file_content_bytes)

            self.log_callback(f"Iniciando envio do arquivo '{file_path}' ({total_file_size} bytes)...")

            self._is_sending_file_flag = True  # Módulo Emissor agora está ativo/enviando

            self._sending_thread = threading.Thread(target=self._process_file_send, args=(
                file_content_bytes,
                total_file_size,
                update_progress_callback,
                update_card_status_callback,
                update_frames_summary_callback,
                cancel_flag,
                on_sending_finished_callback
            ))
            self._sending_thread.start()

            return {"status": "success", "message": "Processo de envio de arquivo iniciado."}
        except Exception as e:
            self.log_callback(f"Erro ao ler arquivo: {e}")
            self._is_sending_file_flag = False  # Reseta a flag em caso de erro na leitura
            if on_sending_finished_callback:
                on_sending_finished_callback('error', f'Erro ao ler arquivo: {e}')
            return {"status": "error", "message": f"Erro ao ler arquivo: {e}"}

    def _process_file_send(self, file_content_bytes, total_file_size,
                            update_progress_callback, update_card_status_callback,
                            update_frames_summary_callback, cancel_flag, on_sending_finished_callback):

        final_status = 'success'
        final_message = 'Envio concluído com sucesso.'

        try:
            if not self.ser or not self.ser.is_open:
                self.log_callback("Serial não conectada. Envio de arquivo não será efetivado.")
                final_status = 'error'
                final_message = 'Serial não conectada durante o envio.'
                self._is_sending_file_flag = False
                return

            num_segments = 0
            total_bytes_sent_original = 0  # Contabiliza apenas os bytes do arquivo original, não o padding/CRC

            if update_progress_callback:
                update_progress_callback(0)

            # Para o envio de arquivos, usaremos um message_id sequencial único
            # Aqui, para simplificar, vamos usar um timestamp ou um contador simples.
            # Em um sistema real, você precisaria de um gerador de message_id mais robusto.
            current_message_id = int(time.time() * 1000) % 256  # Um ID simples (0-255)

            # Calcula o total de fragmentos necessários
            total_fragments = (total_file_size + MAX_PACKET_PAYLOAD_SIZE - 1) // MAX_PACKET_PAYLOAD_SIZE

            self.log_callback(f"Iniciando envio MsgID {current_message_id} ({total_fragments} fragmentos).")

            for i in range(total_fragments):
                if cancel_flag.is_set():
                    self.log_callback("Envio interrompido por solicitação de cancelamento.")
                    final_status = 'cancelled'
                    final_message = 'Envio cancelado pelo usuário.'
                    # Não há um comando "CMD_CANCEL" no Arduino para este protocolo.
                    # Apenas paramos de enviar do lado do Python. O Arduino pode ficar esperando.
                    # Se quisermos sinalizar ao Arduino, poderíamos enviar um pacote especial ou um "EOF" precoce.
                    break

                start_byte = i * MAX_PACKET_PAYLOAD_SIZE
                end_byte = min(start_byte + MAX_PACKET_PAYLOAD_SIZE, total_file_size)
                segment_bytes = file_content_bytes[start_byte:end_byte]

                # Envia o pacote de dados
                result = self._send_packet(
                    PACKET_TYPE_DATA,
                    current_message_id,
                    i,  # fragment_idx
                    total_fragments,
                    segment_bytes
                )

                if result["status"] == "error":
                    self.log_callback(f"Erro ao enviar segmento {i}: {result['message']}")
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