# core/main.py

import sys
import os
import threading
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import webview
from arduino import ArduinoController
from gui import GUIController

# --- Configurações Gerais da Aplicação ---
SERIAL_PORT = 'COM3' # Substitua pelo seu porta serial
# No Linux, pode ser algo como '/dev/ttyUSB0' ou '/dev/ttyACM0'
BAUD_RATE = 9600


class MainApplicationAPI:
    def __init__(self, arduino_controller_instance, log_to_gui_callback, get_webview_window_callback):
        self._arduino_controller = arduino_controller_instance
        self._log_to_gui = log_to_gui_callback
        self._get_webview_window = get_webview_window_callback
        self._update_progress_to_gui = None
        self._update_card_status_to_gui = None
        self._update_frames_summary_to_gui = None
        self._on_sending_finished_to_gui = None

        self.cancel_flag = threading.Event()

        self._backend_start_time = time.time()

    def log_message(self, message):
        # Imprime no console para depuração, independentemente de ser filtrado na GUI
        print(f"[MainApp] {message}")

        # Mensagem específica a ser filtrada da GUI.
        specific_error_to_filter = "Porta serial COM3 NÃO está disponível: could not open port 'COM3': PermissionError(13, 'Acesso negado."
        
        # Se a mensagem contiver o texto do erro específico, não a envia para a GUI.
        if specific_error_to_filter in message:
            print("[MainApp] Mensagem de PermissionError filtrada da GUI.") # Opcional: log para confirmar o filtro no console
            return # Sai da função, impedindo que a mensagem seja enviada para a GUI.

        if self._log_to_gui:
            self._log_to_gui(message)

    def set_progress_callback(self, callback):
        self._update_progress_to_gui = callback

    def set_card_status_callback(self, callback):
        self._update_card_status_to_gui = callback

    def set_frames_summary_callback(self, callback):
        self._update_frames_summary_to_gui = callback

    def set_sending_finished_callback(self, callback):
        self._on_sending_finished_to_gui = callback

    def set_file_received_callback(self, callback):
        pass

    def send_text_message(self, message):
        self.log_message(f"GUI solicitou envio de texto: '{message}'")
        return self._arduino_controller.send_text_message(message)

    def open_file_dialog(self):
        webview_window = self._get_webview_window()
        file_types = ('Todos os arquivos (*.*)', 'Arquivos de Texto (*.txt)')
        result = webview_window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types)
        if result:
            return result[0]
        return None

    def send_file_content(self, file_path):
        self.log_message(f"GUI solicitou envio de arquivo: '{file_path}'")
        if not self._arduino_controller:
            self.log_message("Erro: ArduinoController não inicializado.")
            return {"status": "error", "message": "ArduinoController não inicializado."}

        self.cancel_flag.clear()

        return self._arduino_controller.send_file(
            file_path, self.cancel_flag, self._update_progress_to_gui, self._on_sending_finished_to_gui, self._update_frames_summary_to_gui
        )

    def cancel_file_send(self):
        self.log_message("Solicitação de cancelamento de envio recebida do GUI.")
        self.cancel_flag.set()
        return {"status": "success", "message": "Sinal de cancelamento enviado."}

    def test_ping(self):
        self.log_message("Função 'test_ping' chamada do JavaScript!")
        return "Pong! Resposta do Python."

    def get_connectivity_status(self):
        """
        Retorna um dicionário com o status de todos os componentes de conectividade.
        Os status são inferidos do ArduinoController.
        """
        computer_status = "OK"

        serial_port_available = self._arduino_controller.test_serial_port_availability()
        serial_port_open = self._arduino_controller.is_serial_port_open()

        if serial_port_open:
            serial_status = "Conectado"
        elif serial_port_available:
            serial_status = "Disponível, mas Não Conectada"
        else:
            serial_status = "Indisponível/Erro"

        arduino_status = self._arduino_controller.get_arduino_connection_status()

        emitter_status = self._arduino_controller.get_emitter_status()

        receiver_status = self._arduino_controller.get_receiver_status()

        status_data = {
            "computerStatus": computer_status,
            "serialPortStatus": serial_status,
            "arduinoStatus": arduino_status,
            "emitterStatus": emitter_status,
            "receiverStatus": receiver_status
        }
        self.log_message(f"Status de Conectividade Reportado: {status_data}")
        return status_data

    def get_arduino_connection_only_status(self):
        """Retorna apenas o status da conexão principal do Arduino."""
        return self._arduino_controller.get_arduino_connection_status()

    def get_emitter_module_status(self):
        """Retorna o status específico do módulo Emissor (antena de transmissão)."""
        return self._arduino_controller.get_emitter_status()

    def get_receiver_module_status(self):
        """Retorna o status específico do módulo Receptor (antena de recepção)."""
        return self._arduino_controller.get_receiver_status()

    def get_full_arduino_device_status(self):
        """
        Retorna o status consolidado do Arduino e seus módulos (antenas)
        usando a nova função do ArduinoController.
        """
        return self._arduino_controller.get_overall_arduino_status()

# --- Início da Aplicação ---
if __name__ == '__main__':
    # 1. Instancie o ArduinoController primeiro
    arduino_controller = ArduinoController(
        serial_port=SERIAL_PORT,
        baud_rate=BAUD_RATE,
        log_callback=None,  # Será atualizado depois
    )

    # 2. Instancie o GUIController primeiro, com callbacks temporários
    gui_controller = GUIController(
        main_app_api_instance=None,  # Será atualizado depois
        log_callback=None
    )

    # 3. Instancie a MainApplicationAPI, agora já pode passar o gui_controller
    main_app_api = MainApplicationAPI(
        arduino_controller_instance=arduino_controller,
        log_to_gui_callback=None,  # Será atualizado depois
        get_webview_window_callback=lambda: gui_controller.window
    )

    # 4. Atualize as referências cruzadas
    gui_controller.main_app_api = main_app_api
    gui_controller.log_callback = main_app_api.log_message

    main_app_api.log_to_gui_callback = gui_controller.update_log_in_js
    arduino_controller.log_callback = main_app_api.log_message
    arduino_controller.on_file_received_callback = gui_controller.on_file_received_in_js

    main_app_api.set_progress_callback(gui_controller.update_progress_in_js)
    main_app_api.set_card_status_callback(gui_controller.update_card_status_in_js)
    main_app_api.set_frames_summary_callback(gui_controller.update_frames_summary_in_js)
    main_app_api.set_sending_finished_callback(gui_controller.on_sending_finished_in_js)
    main_app_api.set_file_received_callback(gui_controller.on_file_received_in_js)

    # 5. Criar a janela PyWebView
    main_window = gui_controller.create_window()

    # 6. Iniciar o webview
    webview.start()

    # --- Encerramento da Aplicação ---
    arduino_controller.disconnect()
    print("Aplicação encerrada.")