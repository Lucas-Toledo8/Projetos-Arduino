// arduino_transceiver_bridge.ino vr-1.1
// Projeto 2.0 - Arduino Transceptor: Ponte Bidirecional Serial-RF com VirtualWire, CRC-4, ARQ Selective Repeat e CSMA/CA

// Inclua a biblioteca VirtualWire
#include <VirtualWire.h>
#include <avr/pgmspace.h>  // Necessário para PROGMEM

// ====================================================================================
// CONFIGURAÇÕES DE HARDWARE E PINOS
// ====================================================================================
#define RX_DATA_PIN 2   // Pino de dados para o receptor RF (módulo RX)
#define TX_DATA_PIN 12  // Pino de dados para o transmissor RF (módulo TX)
// ====================================================================================


// ====================================================================================
// DEFINIÇÕES E ESTRUTURAS DO PROTOCOLO
// ====================================================================================
#define PACKET_TYPE_DATA 0x01  // Pacotes contendo dados de arquivo ou status (diferenciados por message_id)
#define PACKET_TYPE_ACK 0x02   // Pacotes de confirmação (Acknowledgement)
#define PACKET_TYPE_NACK 0x03  // Pacotes de não confirmação (Negative Acknowledgement)

// NOVO: IDs de Mensagem Específicos para Pacotes de Status (usados com PACKET_TYPE_DATA)
#define MESSAGE_ID_COMBINED_STATUS 252  // ID para o pacote de status combinado (TX e RX no mesmo pacote)

// Overhead fixo do pacote (campos antes do payload_data e crc_value)
// packet_type (1), message_id (1), fragment_idx (1), total_fragments (1), payload_len (1) = 5 bytes
#define PACKET_FIXED_OVERHEAD_EXCL_CRC 5

// Tamanho máximo do payload que podemos colocar em nosso Packet: VW_MAX_PAYLOAD (27) - PACKET_FIXED_OVERHEAD_EXCL_CRC (5) - crc_value (1) = 21 bytes
#define MAX_PACKET_PAYLOAD_SIZE (VW_MAX_PAYLOAD - PACKET_FIXED_OVERHEAD_EXCL_CRC - 1)  // VW_MAX_PAYLOAD é 27. Resulta em 21 bytes

// Estrutura do Pacote de Protocolo
struct Packet {
  uint8_t packet_type;                            // Tipo do pacote (DATA, ACK, NACK)
  uint8_t message_id;                             // ID da mensagem (identifica uma sequência de fragmentos de arquivo ou tipo de status)
  uint8_t fragment_idx;                           // Índice do fragmento dentro da mensagem
  uint8_t total_fragments;                        // Número total de fragmentos para esta mensagem
  uint8_t payload_len;                            // Comprimento do payload_data (0 a MAX_PACKET_PAYLOAD_SIZE)
  uint8_t payload_data[MAX_PACKET_PAYLOAD_SIZE];  // Dados do payload
  uint8_t crc_value;                              // Valor do CRC-4 para este pacote
};

// Uma verificação em tempo de compilação para garantir que o tamanho da struct Packet não exceda o limite da VirtualWire
char packet_size_check[(sizeof(Packet) <= VW_MAX_PAYLOAD) ? 1 : -1];
// Se sizeof(Packet) for maior que VW_MAX_PAYLOAD (27), isso causará um erro de compilação,
// garantindo que nossa estrutura cabe na transmissão RF.

// --- Variáveis para ARQ (Automatic Repeat Request) ---
#define MAX_UNACKED_FRAGMENTS 4        // Número máximo de fragmentos DATA que podem estar pendentes de ACK
#define RETRANSMISSION_TIMEOUT 700     // Tempo em ms para esperar por um ACK antes de retransmitir
#define MAX_RETRANSMISSION_ATTEMPTS 5  // Número máximo de tentativas de retransmissão antes de desistir

// Estrutura para rastrear fragmentos DATA não confirmados
struct UnackedFragmentInfo {
  Packet packet;                    // Cópia do pacote original
  unsigned long last_sent_time;     // Timestamp da última tentativa de envio
  uint8_t retransmission_attempts;  // Contador de tentativas de retransmissão
  bool active;                      // Indica se esta entrada está em uso no buffer
};

UnackedFragmentInfo unacked_fragments_buffer[MAX_UNACKED_FRAGMENTS];  // Buffer de fragmentos pendentes
uint8_t unacked_count = 0;                                            // Contador de fragmentos pendentes de ACK

// --- Variáveis para CSMA/CA (Random Backoff) ---
static unsigned long backoff_end_time = 0;  // Timestamp em que o backoff deve terminar.
#define MIN_BACKOFF_TIME 1000               // Mínimo de tempo de backoff em ms
#define MAX_BACKOFF_TIME 5000               // Máximo de tempo de backoff em ms

// --- Variáveis para Entrada Serial do Python (para receber pacotes para enviar) ---
#define MAX_SERIAL_INPUT_SIZE sizeof(Packet)  // Espera a estrutura Packet completa do Python
uint8_t serial_input_buffer[MAX_SERIAL_INPUT_SIZE];
// ====================================================================================

// ====================================================================================
// --- DEFINIÇÕES DE ENUMS E VARIÁVEIS DE STATUS PARA EMISSOR E RECEPTOR ---
// NOVO: Usando 'enum class' para um namespace mais limpo e segurança de tipo
// ====================================================================================
enum class ReceiverState : uint8_t {  // Explicitamente uint8_t para garantir o tamanho de 1 byte
  DESCONECTADO = 0,                   // Módulo RF RX não inicializado ou sem comunicação serial
  CONECTADO_AGUARDANDO = 1,           // Módulo RF RX inicializado, aguardando sinal
  RECEBENDO_DADOS = 2,                // Ativamente recebendo fragmentos de arquivo
  ERRO_COMUNICACAO = 3,               // Erro persistente na comunicação RF (ex: muitos NACKs, timeouts)
  SINAL_PERDIDO = 4,                  // Nenhuma atividade RF detectada por um tempo (RX não ouve nada)
  SINAL_FRACO = 5,                    // (Opcional, depende de RSSI, mas mantido para consistência)
  RECEBIDO_COMPLETO = 6               // Um arquivo foi recebido com sucesso
};

enum class EmitterState : uint8_t {  // Explicitamente uint8_t para garantir o tamanho de 1 byte
  DESCONECTADO = 0,                  // Módulo RF TX não inicializado ou sem comunicação serial
  CONECTADO_OCIO = 1,                // Módulo RF TX inicializado, pronto para enviar, mas ocioso
  ENVIANDO_DADOS = 2,                // Ativamente transmitindo fragmentos de arquivo
  ERRO_COMUNICACAO = 3,              // Erro persistente na comunicação RF (ex: muitos retransmissões falhas)
  ERRO_TRANSMISSAO = 4,              // (Opcional, erro de TX de baixo nível)
  AGUARDANDO_ACK = 5,                // Enviou dados e está esperando ACK
  ENVIADO_COMPLETO = 6               // Um arquivo foi enviado com sucesso
};

ReceiverState currentReceiverState = ReceiverState::DESCONECTADO;  // Estado inicial do receptor
EmitterState currentEmitterState = EmitterState::DESCONECTADO;     // Estado inicial do emissor

// Variáveis de controle para status
bool isSendingFile = false;            // Flag: O Arduino está atualmente tentando enviar um arquivo (comando do Python)
bool isReceivingFile = false;          // Flag: O Arduino está atualmente recebendo um arquivo
unsigned long lastRfReceiveTime = 0;   // Tempo da última recepção de qualquer pacote RF (para status de sinal)
#define NO_SIGNAL_TIMEOUT_RX 5000      // Tempo em ms sem receber nada para considerar "sinal perdido" no RX
unsigned long lastStatusSendTime = 0;  // Para controle do envio periódico de status ao Python
#define STATUS_SEND_INTERVAL 1000      // Intervalo em ms para enviar o status combinado para o Python
// ====================================================================================


// ====================================================================================
// FUNÇÃO DE CÁLCULO DE CRC-4
// ====================================================================================
// Tabela de lookup para CRC-4 (polinômio G(x) = x^4 + x + 1, ou 0x3 com x^4 implícito)
const uint8_t crc4_table[] PROGMEM = {
  0x0, 0x3, 0x6, 0x5, 0xC, 0xF, 0xA, 0x9, 0xB, 0x8, 0xD, 0xE, 0x7, 0x4, 0x1, 0x2
};

uint8_t calculateCRC4(const uint8_t* data, uint8_t length) {
  uint8_t crc = 0x00;  // Valor inicial do CRC
  for (uint8_t i = 0; i < length; i++) {
    uint8_t current_byte = data[i];
    // Processa os 4 bits mais significativos
    crc = pgm_read_byte(&(crc4_table[crc ^ (current_byte >> 4)]));
    // Processa os 4 bits menos significativos
    crc = pgm_read_byte(&(crc4_table[crc ^ (current_byte & 0x0F)]));
  }
  return crc;
}
// ====================================================================================


// ====================================================================================
// FUNÇÕES AUXILIARES DE ENVIO DE PACOTES RF
// Estas funções são APENAS para comunicação RF (VirtualWire)
// ====================================================================================
// Função para enviar um pacote via RF
void sendPacket(Packet& pkt, bool is_retransmission = false) {
  // NOVO: Atualiza status do Emissor
  if (pkt.packet_type == PACKET_TYPE_DATA) {
    // Se estamos enviando dados e não estamos já no estado de "ENVIANDO_DADOS", atualizamos
    if (currentEmitterState != EmitterState::ENVIANDO_DADOS && currentEmitterState != EmitterState::AGUARDANDO_ACK) {
      currentEmitterState = EmitterState::ENVIANDO_DADOS;
    }
  } else if (pkt.packet_type == PACKET_TYPE_ACK || pkt.packet_type == PACKET_TYPE_NACK) {
    // ACKs/NACKs são rápidos. O emissor está CONECTADO para poder enviá-los.
    // Se estiver em DESCONECTADO, muda para CONECTADO_OCIO (o link RF está ativo).
    if (currentEmitterState == EmitterState::DESCONECTADO) {
      currentEmitterState = EmitterState::CONECTADO_OCIO;
    }
  }

  // Verifica se o tamanho do pacote excede o limite da VirtualWire (VW_MAX_PAYLOAD é 27)
  if (sizeof(Packet) > VW_MAX_PAYLOAD) {
    Serial.println(F("ERRO INTERNO: Pacote completo (struct Packet) EXCEDEU VW_MAX_PAYLOAD! Nao sera enviado via RF."));
    return;
  }

  // Envia o pacote via VirtualWire
  vw_send((uint8_t*)&pkt, sizeof(Packet));
  vw_wait_tx();  // Aguarda até que a transmissão seja concluída (parte do Carrier Sense)

  // Mensagens de debug para o Serial
  Serial.print(F("RF -> "));
  Serial.print(is_retransmission ? F("RETX") : F("NOVO"));
  Serial.print(F(" | Tipo: 0x"));
  Serial.print(pkt.packet_type, HEX);
  Serial.print(F(", MsgID: "));
  Serial.print(pkt.message_id);
  Serial.print(F(", Frag: "));
  Serial.print(pkt.fragment_idx);
  Serial.print(F("/"));
  Serial.print(pkt.total_fragments);
  Serial.print(F(", P-Len: "));
  Serial.print(pkt.payload_len);
  Serial.print(F(", CRC: 0x"));
  Serial.print(pkt.crc_value, HEX);
  Serial.println(F(")."));

  // Lógica ARQ: Adiciona ao buffer de não confirmados se for um pacote de DADOS NOVO
  if (pkt.packet_type == PACKET_TYPE_DATA && !is_retransmission) {
    isSendingFile = true;  // Definir que um envio de arquivo está em andamento (Python solicitou)
    if (unacked_count < MAX_UNACKED_FRAGMENTS) {
      for (uint8_t i = 0; i < MAX_UNACKED_FRAGMENTS; i++) {
        if (!unacked_fragments_buffer[i].active) {
          memcpy(&(unacked_fragments_buffer[i].packet), &pkt, sizeof(Packet));
          unacked_fragments_buffer[i].last_sent_time = millis();
          unacked_fragments_buffer[i].retransmission_attempts = 0;
          unacked_fragments_buffer[i].active = true;
          unacked_count++;
          currentEmitterState = EmitterState::AGUARDANDO_ACK;  // Emissor agora aguarda ACK
          break;
        }
      }
    } else {
      Serial.println(F("AVISO: Buffer de nao confirmados cheio. Nao foi possivel adicionar novo frag para ARQ."));
      // Em um sistema real, isso deveria pausar o envio de novos pacotes do Python.
    }
  } else if (pkt.packet_type == PACKET_TYPE_DATA && is_retransmission) {
    // Se for uma retransmissão, atualiza o tempo e a contagem de tentativas no buffer
    for (uint8_t i = 0; i < MAX_UNACKED_FRAGMENTS; i++) {
      if (unacked_fragments_buffer[i].active && unacked_fragments_buffer[i].packet.message_id == pkt.message_id && unacked_fragments_buffer[i].packet.fragment_idx == pkt.fragment_idx) {
        unacked_fragments_buffer[i].last_sent_time = millis();
        unacked_fragments_buffer[i].retransmission_attempts++;
        currentEmitterState = EmitterState::AGUARDANDO_ACK;  // Emissor continua aguardando ACK
        break;
      }
    }
  }
}

// Função para enviar um pacote ACK ou NACK via RF
void sendAckNack(uint8_t type, uint8_t msg_id, uint8_t frag_idx) {
  Packet ack_nack_pkt;
  ack_nack_pkt.packet_type = type;
  ack_nack_pkt.message_id = msg_id;
  ack_nack_pkt.fragment_idx = frag_idx;
  ack_nack_pkt.total_fragments = 0;  // Não relevante para ACK/NACK
  ack_nack_pkt.payload_len = 0;      // Não relevante para ACK/NACK

  // Calcula o CRC4 para o ACK/NACK (apenas os 3 primeiros bytes são usados para CRC)
  uint8_t data_for_crc[3];
  data_for_crc[0] = ack_nack_pkt.packet_type;
  data_for_crc[1] = ack_nack_pkt.message_id;
  data_for_crc[2] = ack_nack_pkt.fragment_idx;
  ack_nack_pkt.crc_value = calculateCRC4(data_for_crc, 3);

  sendPacket(ack_nack_pkt);  // Envia o pacote ACK/NACK via RF
}
// ====================================================================================


// ====================================================================================
// FUNÇÃO PARA ENVIAR O STATUS ATUAL DO EMISSOR E RECEPTOR PARA O PYTHON (VIA SERIAL)
// ====================================================================================
void sendCurrentStatusToPython() {
  Packet status_pkt;
  status_pkt.packet_type = PACKET_TYPE_DATA;           // Usamos DATA type, mas diferenciamos pelo message_id
  status_pkt.message_id = MESSAGE_ID_COMBINED_STATUS;  // ID específico para o pacote de status combinado
  status_pkt.fragment_idx = 0;                         // Não relevante para status
  status_pkt.total_fragments = 0;                      // Não relevante para status
  status_pkt.payload_len = 2;                          // O payload são 2 bytes: [emitter_status, receiver_status]

  // Convertemos os enums para seus valores uint8_t subjacentes
  status_pkt.payload_data[0] = static_cast<uint8_t>(currentEmitterState);   // Primeiro byte: status do Emissor
  status_pkt.payload_data[1] = static_cast<uint8_t>(currentReceiverState);  // Segundo byte: status do Receptor

  // O CRC deve ser calculado APENAS sobre os bytes relevantes do pacote, conforme definido pelo Python.
  // Para este pacote de status (que é um PACKET_TYPE_DATA com MESSAGE_ID_COMBINED_STATUS),
  // o CRC inclui: type, id, frag_idx, total_frags, payload_len, e os 'payload_len' bytes do payload_data.
  // Como payload_len é 2, teremos 5 + 2 = 7 bytes para o CRC.
  uint8_t data_for_crc_status[PACKET_FIXED_OVERHEAD_EXCL_CRC + status_pkt.payload_len];
  data_for_crc_status[0] = status_pkt.packet_type;
  data_for_crc_status[1] = status_pkt.message_id;
  data_for_crc_status[2] = status_pkt.fragment_idx;
  data_for_crc_status[3] = status_pkt.total_fragments;
  data_for_crc_status[4] = status_pkt.payload_len;
  data_for_crc_status[5] = status_pkt.payload_data[0];
  data_for_crc_status[6] = status_pkt.payload_data[1];

  status_pkt.crc_value = calculateCRC4(data_for_crc_status, sizeof(data_for_crc_status));

  // Envia o pacote de status via Serial para o Python (NÃO VIA RF)
  Serial.write((uint8_t*)&status_pkt, sizeof(Packet));

  // Para debug no monitor serial (opcional)
  // Serial.print("SERIAL -> Status Binario: TX="); Serial.print(status_pkt.payload_data[0]);
  // Serial.print(" ("); Serial.print(static_cast<uint8_t>(currentEmitterState)); Serial.print(")");
  // Serial.print(", RX="); Serial.print(status_pkt.payload_data[1]);
  // Serial.print(" ("); Serial.print(static_cast<uint8_t>(currentReceiverState)); Serial.print(")");
  // Serial.print(", CRC=0x"); Serial.print(status_pkt.crc_value, HEX);
  // Serial.println(".");
}
// ====================================================================================


// ====================================================================================
// FUNÇÕES DE SETUP
// ====================================================================================
void setup() {
  Serial.begin(9600);  // Inicia a comunicação serial para debug e interface com Python
  Serial.println(F("--- Arduino TRANSCEPTOR BRIDGE: Inicializando com VirtualWire, ARQ e CSMA/CA ---"));

  vw_set_rx_pin(RX_DATA_PIN);  // Configura o pino de recepção
  vw_setup(2000);              // Configura a velocidade de comunicação em bps (bits por segundo)
  vw_rx_start();               // Inicia o modo de recepção da VirtualWire

  vw_set_tx_pin(TX_DATA_PIN);  // Configura o pino de transmissão

  randomSeed(analogRead(A0));  // Inicializa o gerador de números aleatórios para o CSMA/CA

  Serial.println(F("VirtualWire configurado (RX no pino 2, TX no pino 12)."));
  Serial.println(F("Pronto para receber pacotes RF E pacotes SERIAL do Python para envio."));
  Serial.print(F("Tamanho da estrutura Packet: "));
  Serial.print(sizeof(Packet));
  Serial.println(F(" bytes (Max RF Payload: 27)."));
  Serial.print(F("MAX_PACKET_PAYLOAD_SIZE (nosso payload): "));
  Serial.print(MAX_PACKET_PAYLOAD_SIZE);
  Serial.println(F(" bytes."));
  Serial.print(F("ARQ: Max fragmentos nao confirmados: "));
  Serial.print(MAX_UNACKED_FRAGMENTS);
  Serial.println(F("."));
  Serial.print(F("ARQ: Timeout de retransmissao: "));
  Serial.print(RETRANSMISSION_TIMEOUT);
  Serial.println(F(" ms."));
  Serial.print(F("ARQ: Max tentativas: "));
  Serial.print(MAX_RETRANSMISSION_ATTEMPTS);
  Serial.println(F(" tentativas."));
  Serial.print(F("CSMA/CA: Min Backoff: "));
  Serial.print(MIN_BACKOFF_TIME);
  Serial.println(F(" ms."));
  Serial.print(F("CSMA/CA: Max Backoff: "));
  Serial.print(MAX_BACKOFF_TIME);
  Serial.println(F(" ms."));

  // Limpa o buffer de fragmentos não confirmados
  for (uint8_t i = 0; i < MAX_UNACKED_FRAGMENTS; i++) {
    unacked_fragments_buffer[i].active = false;
  }

  // NOVO: Define o status inicial após a inicialização.
  // Se chegou aqui, o hardware está inicializado.
  currentEmitterState = EmitterState::CONECTADO_OCIO;
  currentReceiverState = ReceiverState::CONECTADO_AGUARDANDO;
}
// ====================================================================================


// ====================================================================================
// FUNÇÕES DE LOOP (EXECUÇÃO CONTÍNUA)
// ====================================================================================
void loop() {
  // --- Atualização do status "sinal perdido" para o Receptor ---
  // Se o receptor está CONECTADO_AGUARDANDO e não recebeu nada por um tempo definido.
  if (currentReceiverState == ReceiverState::CONECTADO_AGUARDANDO && (millis() - lastRfReceiveTime > NO_SIGNAL_TIMEOUT_RX)) {
    currentReceiverState = ReceiverState::SINAL_PERDIDO;
  }
  // Se o receptor estava em SINAL_PERDIDO e algo for recebido (atualiza lastRfReceiveTime),
  // a lógica de recebimento abaixo fará a transição de volta para RECEBENDO_DADOS.

  // Buffer para receber o pacote completo (a estrutura Packet) da RF
  uint8_t received_buffer_rf[sizeof(Packet)];
  uint8_t received_buffer_rf_len = sizeof(received_buffer_rf);

  // --- Lógica de Recebimento RF (Maior Prioridade) ---
  if (vw_get_message(received_buffer_rf, &received_buffer_rf_len)) {
    // Algo foi recebido via RF, então o canal estava ativo.
    backoff_end_time = 0;          // Reseta qualquer backoff pendente
    lastRfReceiveTime = millis();  // Atualiza o tempo da última recepção RF

    // NOVO: Atualiza status do Receptor para "Recebendo" se não estava já no estado final
    if (currentReceiverState != ReceiverState::RECEBIDO_COMPLETO) {
      currentReceiverState = ReceiverState::RECEBENDO_DADOS;
      isReceivingFile = true;  // Assumimos que está recebendo um arquivo, a menos que seja um ACK/NACK
    }


    Serial.print(F("RF <- Pacote RF Recebido ("));
    Serial.print(received_buffer_rf_len);
    Serial.print(F(" bytes)... "));

    // Verifica se o tamanho do pacote recebido é o esperado para a nossa estrutura Packet
    if (received_buffer_rf_len == sizeof(Packet)) {
      Packet received_packet;
      memcpy(&received_packet, received_buffer_rf, sizeof(Packet));  // Copia os bytes para a estrutura Packet

      // Determina o comprimento dos dados para o cálculo do CRC (varia se é DATA ou ACK/NACK)
      // Este cálculo deve ser idêntico ao que o EMISSOR usou para gerar o CRC.
      uint8_t data_to_crc_len;
      if (received_packet.packet_type == PACKET_TYPE_DATA) {
        // Para pacotes DATA, o CRC é sobre: type, id, frag_idx, total_frags, payload_len, e o payload
        data_to_crc_len = PACKET_FIXED_OVERHEAD_EXCL_CRC + received_packet.payload_len;
      } else {
        // Para ACK/NACK, o CRC é sobre: type, message_id, fragment_idx
        data_to_crc_len = 3;
      }

      uint8_t data_to_crc[data_to_crc_len];
      data_to_crc[0] = received_packet.packet_type;
      data_to_crc[1] = received_packet.message_id;
      data_to_crc[2] = received_packet.fragment_idx;
      if (received_packet.packet_type == PACKET_TYPE_DATA) {
        data_to_crc[3] = received_packet.total_fragments;
        data_to_crc[4] = received_packet.payload_len;
        if (received_packet.payload_len > 0) {
          memcpy(&data_to_crc[5], received_packet.payload_data, received_packet.payload_len);
        }
      }
      uint8_t calculated_crc = calculateCRC4(data_to_crc, data_to_crc_len);  // Calcula o CRC

      // Exibe informações do pacote recebido (debug)
      Serial.print(F("Tipo: 0x"));
      Serial.print(received_packet.packet_type, HEX);
      Serial.print(F(", MsgID: "));
      Serial.print(received_packet.message_id);
      Serial.print(F(", Frag: "));
      Serial.print(received_packet.fragment_idx);
      Serial.print(F("/"));
      Serial.print(received_packet.total_fragments);
      Serial.print(F(", P-Len: "));
      Serial.print(received_packet.payload_len);
      Serial.print(F(", CRC R: 0x"));
      Serial.print(received_packet.crc_value, HEX);
      Serial.print(F(", CRC C: 0x"));
      Serial.print(calculated_crc, HEX);
      Serial.print(F(" -> "));

      // Verifica o CRC (da transmissão RF)
      if (calculated_crc == received_packet.crc_value) {
        Serial.println(F("CRC OK!"));

        if (received_packet.packet_type == PACKET_TYPE_DATA) {
          // Se for um pacote de DADOS e o CRC estiver OK, envia ACK de volta via RF
          sendAckNack(PACKET_TYPE_ACK, received_packet.message_id, received_packet.fragment_idx);

          // Envia a estrutura Packet COMPLETA para o Python via Serial
          Serial.write((uint8_t*)&received_packet, sizeof(Packet));
          Serial.print(F("SERIAL -> Pacote DATA (MsgID: "));
          Serial.print(received_packet.message_id);
          Serial.print(F(", Frag: "));
          Serial.print(received_packet.fragment_idx);
          Serial.print(F(") enviado ao Python (Tamanho: "));
          Serial.print(sizeof(Packet));
          Serial.println(F(" bytes)."));

          // NOVO: Se o último fragmento de um arquivo foi recebido com sucesso
          if (received_packet.fragment_idx == received_packet.total_fragments - 1 && received_packet.total_fragments > 0) {
            currentReceiverState = ReceiverState::RECEBIDO_COMPLETO;
            isReceivingFile = false;  // Finalizou a recepção do arquivo
          }

        } else if (received_packet.packet_type == PACKET_TYPE_ACK) {
          // Se for um ACK, procura no buffer de não confirmados e remove
          Serial.print(F("ACK Recebido para MsgID "));
          Serial.print(received_packet.message_id);
          Serial.print(F(", Frag "));
          Serial.print(received_packet.fragment_idx);
          Serial.println(F("."));
          for (uint8_t i = 0; i < MAX_UNACKED_FRAGMENTS; i++) {
            if (unacked_fragments_buffer[i].active && unacked_fragments_buffer[i].packet.message_id == received_packet.message_id && unacked_fragments_buffer[i].packet.fragment_idx == received_packet.fragment_idx) {
              unacked_fragments_buffer[i].active = false;
              unacked_count--;
              Serial.print(F("Fragmento MsgID "));
              Serial.print(received_packet.message_id);
              Serial.print(F(", Frag "));
              Serial.print(received_packet.fragment_idx);
              Serial.println(F(" confirmado."));
              break;
            }
          }
          // NOVO: Se todos os fragmentos foram confirmados (buffer vazio) E estava enviando um arquivo
          if (unacked_count == 0 && isSendingFile) {
            currentEmitterState = EmitterState::ENVIADO_COMPLETO;
            isSendingFile = false;  // Finalizou o envio do arquivo
          } else if (unacked_count > 0 && currentEmitterState == EmitterState::AGUARDANDO_ACK) {
            // Ainda tem fragmentos a confirmar, então continua em "enviando" ou "aguardando ACK"
            currentEmitterState = EmitterState::ENVIANDO_DADOS;  // Volta para enviar o próximo ou aguardar outro ACK
          }


        } else if (received_packet.packet_type == PACKET_TYPE_NACK) {
          // Se for um NACK, força a retransmissão do fragmento correspondente
          Serial.print(F("NACK Recebido para MsgID "));
          Serial.print(received_packet.message_id);
          Serial.print(F(", Frag "));
          Serial.print(received_packet.fragment_idx);
          Serial.println(F(". Forcando retransmissao."));
          for (uint8_t i = 0; i < MAX_UNACKED_FRAGMENTS; i++) {
            if (unacked_fragments_buffer[i].active && unacked_fragments_buffer[i].packet.message_id == received_packet.message_id && unacked_fragments_buffer[i].packet.fragment_idx == received_packet.fragment_idx) {
              if (unacked_fragments_buffer[i].retransmission_attempts < MAX_RETRANSMISSION_ATTEMPTS) {
                sendPacket(unacked_fragments_buffer[i].packet, true);  // Retransmite o pacote
              } else {
                Serial.print(F("AVISO: Frag "));
                Serial.print(unacked_fragments_buffer[i].packet.fragment_idx);
                Serial.println(F(" atingiu limite de retransmissoes. Falha no envio."));
                unacked_fragments_buffer[i].active = false;
                unacked_count--;
                // Se um fragmento falha persistentemente, aplica backoff para qualquer próxima tentativa de envio
                backoff_end_time = millis() + random(MIN_BACKOFF_TIME, MAX_BACKOFF_TIME);
                Serial.print(F("Aplicando backoff de "));
                Serial.print(backoff_end_time - millis());
                Serial.println(F("ms devido a falha de TX."));
                // NOVO: Se o envio falhou, o emissor volta para CONECTADO_OCIO e sinaliza erro de comunicação
                currentEmitterState = EmitterState::ERRO_COMUNICACAO;  // Estado de erro
                isSendingFile = false;                                 // Parar de considerar que estamos enviando
              }
              break;
            }
          }
        }
      } else {
        Serial.print(F("ERRO DE CRC! (Dados Corrompidos via RF). MsgID: "));
        Serial.print(received_packet.message_id);
        if (received_packet.packet_type == PACKET_TYPE_DATA) {
          Serial.print(F(", Frag: "));
          Serial.print(received_packet.fragment_idx);
          sendAckNack(PACKET_TYPE_NACK, received_packet.message_id, received_packet.fragment_idx);  // Envia NACK se for um pacote de dados corrompido
        }
        Serial.println(F("."));
        // NOVO: Se houve erro de CRC no recebimento, o receptor pode indicar erro de comunicação
        currentReceiverState = ReceiverState::ERRO_COMUNICACAO;
      }
    } else {
      Serial.println(F("Tamanho do pacote RF recebido incompativel. Descartando."));
      currentReceiverState = ReceiverState::ERRO_COMUNICACAO;  // Pacote malformado = erro
    }
  }

  // --- Lógica de Leitura Serial (Recebe a 'Packet' já montada do Python para ENVIAR via RF) ---
  // Só tenta ler se houver bytes suficientes para uma Packet completa E se o buffer ARQ não estiver cheio
  if (Serial.available() >= sizeof(Packet)) {  // Mudamos a condição para que o backoff não bloqueie a leitura.
    // Lemos o pacote da serial primeiro para não perder dados.
    Serial.readBytes(serial_input_buffer, sizeof(Packet));

    Packet pkt_from_python;
    memcpy(&pkt_from_python, serial_input_buffer, sizeof(Packet));  // Copia para a estrutura Packet

    // 1. Determina o comprimento dos dados para o CÁLCULO/VERIFICAÇÃO do CRC (da camada SERIAL)
    uint8_t data_for_crc_len_serial;
    // O pacote de status (MESSAGE_ID_COMBINED_STATUS) é do tipo PACKET_TYPE_DATA,
    // então a lógica do CRC é a mesma dos dados.
    data_for_crc_len_serial = PACKET_FIXED_OVERHEAD_EXCL_CRC + pkt_from_python.payload_len;

    // 2. Prepara os dados para o CÁLCULO do CRC da camada SERIAL
    uint8_t data_to_verify_serial[data_for_crc_len_serial];
    data_to_verify_serial[0] = pkt_from_python.packet_type;
    data_to_verify_serial[1] = pkt_from_python.message_id;
    data_to_verify_serial[2] = pkt_from_python.fragment_idx;
    data_to_verify_serial[3] = pkt_from_python.total_fragments;
    data_to_verify_serial[4] = pkt_from_python.payload_len;
    if (pkt_from_python.payload_len > 0) {
      memcpy(&data_to_verify_serial[5], pkt_from_python.payload_data, pkt_from_python.payload_len);
    }
    uint8_t calculated_crc_serial = calculateCRC4(data_to_verify_serial, data_for_crc_len_serial);

    // 3. VERIFICAÇÃO DO CRC-4 RECEBIDO DO PYTHON (PARA GARANTIR INTEGRIDADE DO PACOTE DO PYTHON)
    if (calculated_crc_serial != pkt_from_python.crc_value) {
      Serial.print(F("ERRO CRC SERIAL: Pacote corrompido do Python! (MsgID: "));
      Serial.print(pkt_from_python.message_id);
      Serial.print(F(", Frag: "));
      Serial.print(pkt_from_python.fragment_idx);
      Serial.print(F(", CRC Python: 0x"));
      Serial.print(pkt_from_python.crc_value, HEX);
      Serial.print(F(", CRC Calc: 0x"));
      Serial.print(calculated_crc_serial, HEX);
      Serial.println(F("). Descartando."));
      return;  // Sai do loop e espera o próximo pacote
    }

    // Se o CRC do Python está OK e não estamos em backoff RF e o buffer ARQ não está cheio.
    if (millis() < backoff_end_time || unacked_count >= MAX_UNACKED_FRAGMENTS) {
      Serial.println(F("AVISO: Recebido do Python, mas canal RF ocupado ou buffer cheio. Pacote aguardando..."));
      // Em um sistema real, você não descartaria, mas enfileiraria ou sinalizaria ao Python para pausar.
      // Por enquanto, apenas avisamos e o Python precisaria retransmitir ou tentar novamente.
      return;
    }

    // Se chegamos até aqui, o CRC do Python está OK e podemos tentar enviar via RF.
    Serial.print(F("SERIAL -> Recebido do Python (CRC OK) para enviar RF: Tipo: 0x"));
    Serial.print(pkt_from_python.packet_type, HEX);
    Serial.print(F(", MsgID: "));
    Serial.print(pkt_from_python.message_id);
    Serial.print(F(", Frag: "));
    Serial.print(pkt_from_python.fragment_idx);
    Serial.print(F("/"));
    Serial.print(pkt_from_python.total_fragments);
    Serial.print(F(", P-Len: "));
    Serial.print(pkt_from_python.payload_len);
    Serial.print(F(", CRC (do Python/Para RF): 0x"));
    Serial.print(pkt_from_python.crc_value, HEX);
    Serial.println(F("... Enviando via RF."));

    sendPacket(pkt_from_python);  // Envia o pacote RF com ARQ e CSMA/CA
  }

  // --- Lógica de Retransmissão ARQ (Gerencia timeouts de pacotes já enviados via RF) ---
  for (uint8_t i = 0; i < MAX_UNACKED_FRAGMENTS; i++) {
    if (unacked_fragments_buffer[i].active && (millis() - unacked_fragments_buffer[i].last_sent_time > RETRANSMISSION_TIMEOUT)) {
      if (unacked_fragments_buffer[i].retransmission_attempts < MAX_RETRANSMISSION_ATTEMPTS) {
        Serial.print(F("TIMEOUT! Retransmitindo Frag "));
        Serial.print(unacked_fragments_buffer[i].packet.fragment_idx);
        Serial.print(F(" (Tentativa: "));
        Serial.print(unacked_fragments_buffer[i].retransmission_attempts + 1);
        Serial.println(F(")."));
        sendPacket(unacked_fragments_buffer[i].packet, true);  // Retransmite o pacote
      } else {
        Serial.print(F("ERRO FATAL: Frag "));
        Serial.print(unacked_fragments_buffer[i].packet.fragment_idx);
        Serial.print(F(" da MsgID "));
        Serial.print(unacked_fragments_buffer[i].packet.message_id);
        Serial.println(F(" atingiu limite de retransmissoes. Desistindo deste fragmento."));
        unacked_fragments_buffer[i].active = false;
        unacked_count--;
        backoff_end_time = millis() + random(MIN_BACKOFF_TIME, MAX_BACKOFF_TIME);
        Serial.print(F("Aplicando backoff de "));
        Serial.print(backoff_end_time - millis());
        Serial.println(F("ms devido a falha de retransmissao."));
        // NOVO: Se o envio falhou completamente para um fragmento, sinaliza erro de comunicação do emissor
        currentEmitterState = EmitterState::ERRO_COMUNICACAO;
        isSendingFile = false;  // Parar de considerar que estamos enviando
      }
    }
  }

  // --- Envio periódico de status para o Python (VIA SERIAL APENAS) ---
  if (millis() - lastStatusSendTime >= STATUS_SEND_INTERVAL) {
    sendCurrentStatusToPython();
    lastStatusSendTime = millis();
  }

  // --- Lógica para transições de status (refinada) ---
  // Transição do Emissor:
  // Se não estamos mais enviando um arquivo E não há fragmentos ARQ pendentes E o estado não é finalizado (SENT) ou erro
  if (!isSendingFile && unacked_count == 0) {
    if (currentEmitterState == EmitterState::ENVIANDO_DADOS || currentEmitterState == EmitterState::AGUARDANDO_ACK) {
      currentEmitterState = EmitterState::CONECTADO_OCIO;  // Volta para ocioso
    }
    // Se estava em ENVIADO_COMPLETO, volta para OCIO após um tempo, ou deixa o Python controlar a transição
    if (currentEmitterState == EmitterState::ENVIADO_COMPLETO) {
      // Podemos manter o estado "ENVIADO_COMPLETO" por um curto período ou deixar o Python detectar e redefinir
      // Por simplicidade, faremos a transição imediata para OCIO aqui.
      // Um atraso aqui poderia segurar o estado por um tempo.
      currentEmitterState = EmitterState::CONECTADO_OCIO;
    }
  }

  // Transição do Receptor:
  // Se não estamos mais recebendo um arquivo E o estado não é finalizado (RECEIVED) ou erro
  if (!isReceivingFile) {
    if (currentReceiverState == ReceiverState::RECEBENDO_DADOS) {
      // Após parar de receber, pode voltar para AGUARDANDO ou SINAL_PERDIDO dependendo do tempo
      if (millis() - lastRfReceiveTime > NO_SIGNAL_TIMEOUT_RX) {
        currentReceiverState = ReceiverState::SINAL_PERDIDO;
      } else {
        currentReceiverState = ReceiverState::CONECTADO_AGUARDANDO;
      }
    }
    // Se estava em RECEBIDO_COMPLETO, volta para AGUARDANDO
    if (currentReceiverState == ReceiverState::RECEBIDO_COMPLETO) {
      // Similar ao TX_SENT, podemos manter o estado por um tempo ou deixar o Python resetar
      currentReceiverState = ReceiverState::CONECTADO_AGUARDANDO;
    }
  }
  // Resetar estados de ERRO: O Python deve ter uma maneira de "limpar" o erro, ou a lógica deve ser reavaliada
  // Se o estado é ERRO_COMUNICACAO, ele permanece até que uma nova comunicação bem sucedida ocorra
  // ou o Python envie um comando de reset. Por enquanto, a lógica acima pode sobrescrevê-lo se
  // o sistema voltar a operar. Considere adicionar um reset explícito via comando serial do Python.


  delay(10);  // Pequeno delay para estabilidade
}