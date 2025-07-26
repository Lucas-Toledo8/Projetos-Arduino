# Iniciando o Programa

Primeiro, abra o terminal (Prompt de Comando no Windows, PowerShell no Windows, ou Terminal no Linux/macOS) e **navegue até a pasta raiz do projeto**.

Exemplo:

```bash
cd C:\Caminho\que\voçe\colocou\projeto_vr1,0  # No Windows
```

ou

```bash
cd /home/usuario/Caminho/que/voce/colocou/projeto_vr1,0 # No Linux/macOS
```

## 1 - Ativando o Ambiente Virtual (venv)

**Se estiver usando o PowerShell:**

```powershell
.\venv\Scripts\Activate.ps1
```

**Se estiver usando o CMD:**

```cmd
.\venv\Scripts\Activate
```

**Para desativar o ambiente virtual:**

```bash
deactivate
```

## 2 - Caso não tenha Biblioteca do Python

**Faça este Comando de Instalaçao da Biblioteca pelo Terminal:**

```bash
pip install -r requirements.txt
```

## 3 - Executando o Programa

```bash
python core/main.py
```

## Observação

**Caso tenha que mudar 'Porta Serial' no Python acesse core/main.py:**

```bash
Altere está linha 15 no codigo ex.: 'COMx'
SERIAL_PORT = 'COM3'
```

**Se for no Linux vai depender de qual distro linux as usa porta serial:**

```bash
Altere está linha ex.: '/dev/ttyUSB0' ou '/dev/ttyACM0'
SERIAL_PORT = '/dev/ttyUSB3'
```

**No arduino os pinos definidos são:**

```bash
- Receptor RF (módulo RX)......PINO: 2   
- Transmissor RF (módulo TX)...PINO: 12  
```
