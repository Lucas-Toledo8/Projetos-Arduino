# core/gui.py

print("Carregando gui.py do caminho:", __file__) # <<< Adicione esta linha
import webview
import os
import json # Importar json para update_full_arduino_status_in_js

class GUIController:
    def __init__(self, main_app_api_instance, log_callback=None):
        self.main_app_api = main_app_api_instance
        self.window = None
        self.log_callback = log_callback if log_callback else self._default_log_callback
        self.update_progress_callback = None
        self.update_card_status_callback = None # Este ainda existe para compatibilidade com usos existentes
        self.update_frames_summary_callback = None
        self.on_sending_finished_callback = None
        self.on_file_received_callback = None

    def _default_log_callback(self, message):
        print(f"[GUIController] {message}")

    def _on_window_ready(self):
        self.log_callback("Interface carregada e pronta para comunicação JS-Python.")
        # Define os status iniciais na GUI
        if self.update_card_status_callback:
            self.update_card_status_callback('emitterStatus', 'Parado')
            self.update_card_status_callback('receiverStatus', 'Aguardando...')
        if self.update_frames_summary_callback:
            self.update_frames_summary_callback('N/A', 'N/A')
        if self.update_progress_callback:
            self.update_progress_callback(0)

        # ✨✨✨ NOVO: Chamar uma função JS para solicitar a atualização inicial dos status consolidados ✨✨✨
        # Isso garantirá que os cards de status sejam preenchidos logo no início
        # Esta função JS chamará as APIs get_arduino_connection_only_status(), get_emitter_module_status(), etc.
        if self.window:
            self.window.evaluate_js('requestAndUpdateAllArduinoStatus();') # Você precisará criar esta função no seu JS

    def create_window(self):
        html_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gui', 'index.html')
        html_url = f'file://{html_file_path}'

        self.window = webview.create_window(
            'Projeto Pratico',
            url=html_url,
            js_api=self.main_app_api,
            resizable=True,
            maximized=True
        )

        self.window.events.loaded += self._on_window_ready
        return self.window

    def update_log_in_js(self, message):
        """Executa uma função JavaScript na interface para exibir a mensagem de log."""
        if self.window:
            clean_message = message.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'logMessage("{clean_message}");')

    def update_progress_in_js(self, percentage):
        """Executa uma função JavaScript para atualizar a barra de progresso visualmente."""
        if self.window:
            self.window.evaluate_js(f'updateProgressBar({percentage});')

    def update_card_status_in_js(self, card_id, status_text):
        """
        Este método ainda existe para usos anteriores que dependem de 'card_id'.
        As novas funções de status não o utilizarão diretamente.
        """
        if self.window:
            clean_status_text = status_text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'updateCardStatus("{card_id}", "{clean_status_text}");')

    def update_frames_summary_in_js(self, segments, bytes_value):
        if self.window:
            segments_str = str(segments) if segments is not None else 'N/A'
            bytes_value_str = str(bytes_value) if bytes_value is not None else 'N/A'
            self.window.evaluate_js(f'updateFramesSummary("{segments_str}", "{bytes_value_str}");')

    def on_sending_finished_in_js(self, status, message):
        """Método para o Python chamar o JavaScript quando o envio termina ou é cancelado."""
        if self.window:
            clean_message = message.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'onSendingFinished("{status}", "{clean_message}");')

    def on_file_received_in_js(self, status, file_name, message):
        """
        Método para o Python chamar o JavaScript quando um arquivo é recebido do Arduino.
        """
        if self.window:
            clean_file_name = file_name.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            clean_message = message.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'onFileReceived("{status}", "{clean_file_name}", "{clean_message}");')

    #  NOVOS MÉTODOS DEDICADOS PARA ATUALIZAR STATUS ESPECÍFICOS NO JS 

    def update_arduino_connection_status_display_in_js(self, status_text):
        """
        Atualiza o status da conexão principal do Arduino na GUI usando uma função JS dedicada.
        Assumimos que haverá uma função JS chamada 'updateArduinoConnectionStatusDisplay'.
        """
        if self.window:
            clean_status_text = status_text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'updateArduinoConnectionStatusDisplay("{clean_status_text}");')

    def update_emitter_module_status_display_in_js(self, status_text):
        """
        Atualiza o status do módulo Emissor na GUI usando uma função JS dedicada.
        Assumimos que haverá uma função JS chamada 'updateEmitterModuleStatusDisplay'.
        """
        if self.window:
            clean_status_text = status_text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'updateEmitterModuleStatusDisplay("{clean_status_text}");')

    def update_receiver_module_status_display_in_js(self, status_text):
        """
        Atualiza o status do módulo Receptor na GUI usando uma função JS dedicada.
        Assumimos que haverá uma função JS chamada 'updateReceiverModuleStatusDisplay'.
        """
        if self.window:
            clean_status_text = status_text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
            self.window.evaluate_js(f'updateReceiverModuleStatusDisplay("{clean_status_text}");')

    def update_full_arduino_status_object_in_js(self, status_dict):
        """
        Atualiza todos os status de conexão do Arduino e módulos na GUI com um dicionário
        usando uma função JS dedicada.
        Assumimos que haverá uma função JS chamada 'updateFullArduinoStatusObject'.
        """
        if self.window:
            # Serializa o dicionário Python para uma string JSON válida
            json_status = json.dumps(status_dict)
            self.window.evaluate_js(f'updateFullArduinoStatusObject({json_status});')

    def get_connectivity_status(self):
        return {
            "computer": "OK",  # ou lógica real se quiser
            "serial_port": "Disponível" if self._arduino_controller.test_serial_port_availability() else "Indisponível",
            "arduino": "Conectado" if self._arduino_controller.is_serial_port_open() else "Desconectado",
            "rf_emitter": self._arduino_controller.get_emitter_status(),
            "rf_receiver": self._arduino_controller.get_receiver_status(),
        }


