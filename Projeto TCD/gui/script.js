// gui/script.js

let selectedFilePath = null;
let isSending = false; // Adiciona uma flag para controlar o estado de envio

// Função para logar mensagens (no textarea com id="logArea")
function logMessage(message) {
    const logArea = document.getElementById('logArea');
    if (logArea) {
        logArea.value += `[${new Date().toLocaleTimeString()}] ${message}\n`;
        logArea.scrollTop = logArea.scrollHeight;
    }
    console.log(`[JS Log] ${message}`); // Adiciona log para o console do navegador
}

// Função para atualizar o status de um card específico (como Emissor, Receptor)
// Esta função é chamada pelo Python (via gui.py -> update_card_status_in_js)
function updateCardStatus(cardId, statusText) {
    const statusElement = document.getElementById(cardId);
    // Garante que statusText é string e não undefined/null
    const safeStatusText = (statusText !== undefined && statusText !== null) ? String(statusText) : '';
    if (statusElement) {
        statusElement.textContent = safeStatusText;

        statusElement.classList.remove(
            'status-ok', 'status-sending', 'status-receiving',
            'status-error', 'status-completed', 'status-nok', 'status-warning'
        );

        if (
            safeStatusText.includes('OK') ||
            safeStatusText.includes('Concluído') ||
            safeStatusText.includes('Parado') ||
            safeStatusText.includes('Pronto') ||
            safeStatusText.includes('Conectado')
        ) {
            statusElement.classList.add('status-ok');
        } else if (safeStatusText.includes('Enviando')) {
            statusElement.classList.add('status-sending');
        } else if (safeStatusText.includes('Recebendo') || safeStatusText.includes('Aguardando')) {
            statusElement.classList.add('status-receiving');
        } else if (safeStatusText.includes('Erro') || safeStatusText.includes('error')) {
            statusElement.classList.add('status-error');
        } else if (safeStatusText.includes('NOK')) {
            statusElement.classList.add('status-nok');
        } else if (safeStatusText.includes('Aviso') || safeStatusText.includes('Warning')) {
            statusElement.classList.add('status-warning');
        }
    }
}

// Função para atualizar as novas spans de Pacotes e Tamanho no card Frames
// Esta função é chamada pelo Python (via gui.py -> update_frames_summary_in_js)
function updateFramesSummary(segments, bytes) {
    const packetsCountElement = document.getElementById('packetsCount');
    const totalBytesSentElement = document.getElementById('totalBytesSent');

    if (packetsCountElement) {
        packetsCountElement.textContent = `Pacotes: ${segments}`;
    }
    if (totalBytesSentElement) {
        totalBytesSentElement.textContent = ` ${bytes} bytes`;
    }
}

// -----------------------------------------------------------------------------
// Funções para o Status de Conectividade POST (mantidas como estão)
// -----------------------------------------------------------------------------

// Função para obter e atualizar o status de conectividade (o JS CHAMA o Python periodicamente)
async function updateConnectivityStatus() {
    try {
        const status = await window.pywebview.api.get_connectivity_status();
        document.getElementById('statusComputer').textContent = status.computerStatus;
        document.getElementById('statusSerialPort').textContent = status.serialPortStatus;
        document.getElementById('statusArduino').textContent = status.arduinoStatus === true || status.arduinoStatus === "Conectado" ? "Conectado" : "Desconectado";
        document.getElementById('statusRFEmitter').textContent = status.emitterStatus;
        document.getElementById('statusRFReceiver').textContent = status.receiverStatus;
    } catch (e) {
        document.getElementById('statusComputer').textContent = "Erro";
        document.getElementById('statusSerialPort').textContent = "Erro";
        document.getElementById('statusArduino').textContent = "Erro";
        document.getElementById('statusRFEmitter').textContent = "Erro";
        document.getElementById('statusRFReceiver').textContent = "Erro";
    }
}

// Função auxiliar para aplicar classes CSS com base no status de CONECTIVIDADE (para cores do card POST)
function applyConnectivityStatusClass(element, status) {
    element.classList.remove('status-ok', 'status-nok', 'status-warning');

    if (status === "OK" || status.includes("Conectado") || status.includes("Pronto") || status.includes("Comunicação OK")) {
        element.classList.add('status-ok');
    } else if (status.includes("Erro") || status.includes("Desconectado") || status.includes("Desconectada") || status.includes("Indisponível") || status.includes("Inativo") || status.includes("Não Conectada") || status.includes("Sem Resposta") || status.includes("Erro de Conexão")) {
        element.classList.add('status-nok');
    } else if (status.includes("Verificando") || status.includes("Inicializando") || status.includes("Disponível") || status.includes("Aguardando")) {
        element.classList.add('status-warning');
    }
}

// -----------------------------------------------------------------------------
// FIM das Funções para o Status de Conectividade POST
// -----------------------------------------------------------------------------


// Funções para lidar com a seleção e envio de arquivo
async function sendFile() {
    if (isSending) {
        logMessage("Um envio já está em andamento. Cancele-o primeiro.");
        return;
    }

    try {
        const filePath = await window.pywebview.api.open_file_dialog();
        if (filePath) {
            selectedFilePath = filePath;
            document.getElementById('filePathDisplay').textContent = `Arquivo selecionado: ${filePath.split('\\').pop()}`;
            logMessage(`Ficheiro selecionado: ${filePath}`);
            resetUIForNewSend();
        } else {
            logMessage("Seleção de ficheiro cancelada.");
            document.getElementById('filePathDisplay').textContent = "Nenhum ficheiro selecionado";
            selectedFilePath = null;
        }
    } catch (error) {
        logMessage(`Erro ao abrir diálogo de ficheiro: ${error}`);
    }
}

async function startSendingFile() {
    if (!selectedFilePath) {
        logMessage("Por favor, selecione um ficheiro primeiro.");
        return;
    }
    if (isSending) {
        logMessage("Um envio já está em andamento.");
        return;
    }

    isSending = true;
    document.getElementById('startButton').style.display = 'none';
    document.getElementById('cancelButton').style.display = 'block';

    logMessage("Iniciando processo de envio de arquivo...");
    try {
        updateProgressBar(0);
        updateFramesSummary('N/A', 'N/A');
        updateCardStatus('emitterStatus', 'Enviando'); // <<<<<< MANTIDO COMO ESTAVA ANTES
        
        const response = await window.pywebview.api.send_file_content(selectedFilePath); 
        logMessage(`Status do processo de envio de arquivo: ${response.status} - ${response.message}`);
    } catch (error) {
        logMessage(`Erro ao iniciar envio de arquivo: ${error}`);
        onSendingFinished('error', `Erro ao iniciar envio: ${error}`);
    }
}

async function cancelSendingFile() {
    if (!isSending) {
        logMessage("Nenhum envio em andamento para cancelar.");
        return;
    }
    logMessage("Solicitando cancelamento do envio...");
    try {
        await window.pywebview.api.cancel_file_send(); 
        logMessage("Solicitação de cancelamento enviada. Aguardando confirmação do backend...");
    } catch (error) {
        logMessage(`Erro ao solicitar cancelamento: ${error}`);
        onSendingFinished('error', `Erro ao solicitar cancelamento: ${error}`);
    }
}

// Função que o Python pode chamar para avisar o JS que o envio foi finalizado/cancelado
// Chamada por gui.py -> on_sending_finished_in_js
function onSendingFinished(status, message) {
    logMessage(`Envio finalizado: ${message}`);
    isSending = false;

    if (status === 'cancelled' || status === 'error') {
        document.getElementById('startButton').style.display = 'block';
        document.getElementById('cancelButton').style.display = 'none';
        updateProgressBar(0);
        updateFramesSummary('N/A', 'N/A');
        updateCardStatus('emitterStatus', 'Parado'); // <<<<<< MANTIDO COMO ESTAVA ANTES
    } else if (status === 'success') {
        document.getElementById('startButton').style.display = 'block';
        document.getElementById('cancelButton').style.display = 'none';
        updateCardStatus('emitterStatus', 'Concluído!'); // <<<<<< MANTIDO COMO ESTAVA ANTES
        updateProgressBar(100);
    }
}

// Função que o Python pode chamar quando um arquivo é recebido do Arduino
// Chamada por gui.py -> on_file_received_in_js
function onFileReceived(status, fileName, message) {
    logMessage(`Recepção de arquivo: [${status.toUpperCase()}] ${fileName} - ${message}`);
    updateCardStatus('receiverStatus', status === 'success' ? 'Recebido!' : 'Erro na Recepção!'); // <<<<<< MANTIDO COMO ESTAVA ANTES
    
    if (status === 'success') {
        alert(`Arquivo "${fileName}" recebido com sucesso!\nDetalhes: ${message}`);
    } else {
        alert(`Erro ao receber arquivo "${fileName}":\n${message}`);
    }
    setTimeout(() => {
        updateCardStatus('receiverStatus', 'Aguardando...'); // <<<<<< MANTIDO COMO ESTAVA ANTES
    }, 3000);
}

// Função para resetar a interface para um NOVO envio (quando um arquivo é selecionado)
function resetUIForNewSend() {
    isSending = false;
    document.getElementById('startButton').style.display = 'block';
    document.getElementById('cancelButton').style.display = 'none';
    updateProgressBar(0);
    updateFramesSummary('N/A', 'N/A');
    updateCardStatus('emitterStatus', 'Parado'); // <<<<<< MANTIDO COMO ESTAVA ANTES
    document.getElementById('logArea').value = '';
    logMessage("Interface resetada para novo envio.");
}

// Função para atualizar a barra de progresso E o texto do percentual
// Chamada por gui.py -> update_progress_in_js
function updateProgressBar(percentage) {
    const progressBar = document.getElementById('progressBar');
    const progressPercentageText = document.getElementById('progressPercentageText'); 
    
    if (progressBar && progressPercentageText) { 
        const limitedPercentage = Math.min(100, Math.max(0, percentage)); 
        progressBar.style.width = limitedPercentage + '%';
        progressPercentageText.textContent = limitedPercentage + '%'; 
    }
}

// ✨✨✨ NOVAS FUNÇÕES JS DEDICADAS PARA OS STATUS (ADIÇÕES PUROS) ✨✨✨

// Função dedicada para atualizar o status da conexão principal do Arduino
// Chamada por gui.py -> update_arduino_connection_status_display_in_js
function updateArduinoConnectionStatusDisplay(statusText) {
    const element = document.getElementById('arduinoConnectionStatusDisplay');
    if (element) {
        element.textContent = statusText;
        updateCardStatus('arduinoConnectionStatusDisplay', statusText);
    }
}

// Função dedicada para atualizar o status do módulo Emissor
// Chamada por gui.py -> update_emitter_module_status_display_in_js
function updateEmitterModuleStatusDisplay(statusText) {
    const element = document.getElementById('emitterModuleStatusDisplay');
    if (element) {
        element.textContent = statusText;
        updateCardStatus('emitterModuleStatusDisplay', statusText);
    }
}

// Função dedicada para atualizar o status do módulo Receptor
// Chamada por gui.py -> update_receiver_module_status_display_in_js
function updateReceiverModuleStatusDisplay(statusText) {
    const element = document.getElementById('receiverModuleStatusDisplay');
    if (element) {
        element.textContent = statusText;
        updateCardStatus('receiverModuleStatusDisplay', statusText);
    }
}

// Função dedicada para atualizar todos os status de uma vez, recebendo um objeto
// Chamada por gui.py -> update_full_arduino_status_object_in_js
function updateFullArduinoStatusObject(statusObject) {
    if (statusObject) {
        updateArduinoConnectionStatusDisplay(statusObject.connection_status);
        updateEmitterModuleStatusDisplay(statusObject.emitter_status);
        updateReceiverModuleStatusDisplay(statusObject.receiver_status);
        logMessage(`Status Completo do Arduino Atualizado: Conexão: ${statusObject.connection_status}, Emissor: ${statusObject.emitter_status}, Receptor: ${statusObject.receiver_status}`);
    }
}

// Função assíncrona para solicitar todos os status ao Python e atualizar a GUI
// Esta função será chamada na inicialização e periodicamente
async function requestAndUpdateAllArduinoStatus() {
    try {
        logMessage("Solicitando status consolidado do Arduino...");
        const statusData = await window.pywebview.api.get_full_arduino_device_status();
        updateFullArduinoStatusObject(statusData);
    } catch (error) {
        logMessage(`Erro ao solicitar status consolidado do Arduino: ${error.name}: ${error.message}`);
        console.error("Erro ao obter status consolidado do Arduino:", error);
        updateArduinoConnectionStatusDisplay("Erro de Conexão");
        updateEmitterModuleStatusDisplay("Erro");
        updateReceiverModuleStatusDisplay("Erro");
    }
}

//  FIM DAS NOVAS FUNÇÕES JS 

// Exemplo de função para atualizar o status da porta serial
function updateSerialPortStatus(statusText) {
    const el = document.getElementById('statusSerialPort');
    if (el) el.textContent = statusText;
}

// Exemplo de chamada à API Python
async function refreshSerialPortStatus() {
    try {
        const status = await window.pywebview.api.get_serial_port_status();
        updateSerialPortStatus(status);
    } catch (e) {
        updateSerialPortStatus("Erro");
    }
}

// --- Event Listeners e Inicialização ---
window.addEventListener('pywebviewready', () => {
    // Configurar listeners de botões, etc. (ajuste os IDs conforme seu HTML)
    document.getElementById('startButton').addEventListener('click', startSendingFile);
    document.getElementById('cancelButton').addEventListener('click', cancelSendingFile);
    

    // Listener para o botão de enviar mensagem de texto
    const sendTextButton = document.getElementById('sendTextButton');
    const textMessageInput = document.getElementById('textMessageInput');

    if (sendTextButton && textMessageInput) {
        sendTextButton.addEventListener('click', async () => {
            const message = textMessageInput.value;
            if (message) {
                logMessage(`Enviando mensagem de texto: "${message}"`);
                try {
                    const result = await window.pywebview.api.send_text_message(message);
                    if (result.status === "success") {
                        logMessage("Mensagem de texto enviada com sucesso!");
                        textMessageInput.value = '';
                    } else {
                        logMessage(`Erro ao enviar mensagem de texto: ${result.message}`);
                    }
                } catch (error) {
                    logMessage(`Erro na chamada API send_text_message: ${error}`);
                }
            } else {
                        logMessage("Digite uma mensagem para enviar.");
                    }
                });
            }

    // Inicializa o estado dos botões ao carregar a página
    document.getElementById('startButton').style.display = 'block';
    document.getElementById('cancelButton').style.display = 'none';
    updateProgressBar(0);
    updateFramesSummary('N/A', 'N/A');
    updateCardStatus('emitterStatus', 'Parado'); // <<<<<< MANTIDO COMO ESTAVA ANTES

    // ### Inicia a atualização periódica dos status de conectividade do POST
    // MANTIDO: O setInterval para updateConnectivityStatus ainda está aqui, sem alterações.
    setInterval(updateConnectivityStatus, 10000);
    updateConnectivityStatus(); // Chama uma vez na inicialização para exibir o status imediatamente

    // NOVO: Adiciona a chamada para a nova função de atualização de status consolidado
    setInterval(requestAndUpdateAllArduinoStatus, 11000); // Chama a cada 5 segundos
    requestAndUpdateAllArduinoStatus(); // Chama uma vez na inicialização
});