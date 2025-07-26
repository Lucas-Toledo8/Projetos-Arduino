#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <ESP8266WiFi.h>
#include <MySQL_Connection.h>
#include <MySQL_Cursor.h>
#include <WiFiClientSecure.h>
#include <UniversalTelegramBot.h>
#include <ArduinoJson.h>
#include "DHT.h"                     // DHT lib
#define DHTPIN 3                     // Pino em que será conectado o sensor
#define DHTTYPE DHT11                // Versão do sensor
DHT dht(DHTPIN, DHTTYPE);            // Criação objeto DHT
LiquidCrystal_I2C lcd(0x27, 16, 2);  // set the LCD address to 0x27 for a 16 chars and 2 line display

IPAddress server_addr(0, 0, 0, 0);  // O IP DO SERVIDOR DA CLEVER CLOUD
char user[] = "usuario";            // Usuario MySQL
char password[] = "senha";            //   Senha MySQL

char ssid[] = "nome_do_wifi";         //  Nome de rede Wifi
char pass[] = "senha";  //  Senha Wi-Fi

#define BOT_TOKEN "Bot_Token"
#define CHAT_ID "ID_do_chat"

X509List cert(TELEGRAM_CERTIFICATE_ROOT);
WiFiClientSecure secured_client;
UniversalTelegramBot bot(BOT_TOKEN, secured_client);

char INSERT_DATA[] = "INSERT INTO databese.name_table (colum1, colum2) VALUES (%d,%d)";

WiFiClient client;
MySQL_Connection conn(&client);
MySQL_Cursor* cursor;

int tatual = 0;
int t;
int id = 1;

void setup() {
  Wire.begin(2, 0);
  lcd.init();  // initialize the lcd
  lcd.backlight();
  lcd.clear();
  delay(1000);
  lcd.setCursor(0, 0);
  lcd.print("Iniciando");
  lcd.setCursor(0, 1);
  lcd.print("o sistema");
  pinMode(1, OUTPUT);
  dht.begin();  // Inicializa o sensor
  VerificaWiFi();
  delay(1000);
}

void loop() {
  t = dht.readTemperature();  // Leitura da Temperatura em Celsius

  if (t != tatual) {
    if ((t > 0) && (t < 100)) {
      tatual = t;
      digitalWrite(1, HIGH);
      delay(100);
      digitalWrite(1, LOW);
      delay(100);
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Temperatura");
      lcd.setCursor(12, 0);
      lcd.print(t);
      lcd.setCursor(15, 0);
      lcd.print("C");
      delay(1000);
      if (tatual > 30) {
        delay(100);
        lcd.clear();
        lcd.setCursor(0, 0);
        lcd.print("Alerta de");
        lcd.setCursor(12, 0);
        lcd.print(t);
        lcd.setCursor(15, 0);
        lcd.print("C");
        lcd.setCursor(0, 1);
        lcd.print("temperatura");
        enviaMensagem();
        lcd.clear();
        lcd.setCursor(0, 0);
        lcd.print("Temperatura");
        lcd.setCursor(12, 0);
        lcd.print(t);
        lcd.setCursor(15, 0);
        lcd.print("C");
      }
      EnviaDados(id, tatual);
    }
  }
}
void EnviaDados(int id, int temp) {
  char query[128];
  char temperatura[10];
  //VerificaWiFi();
  if (conn.connect(server_addr, 3306, user, password)) {
    delay(1000);
    MySQL_Cursor* cur_mem = new MySQL_Cursor(&conn);
    // Save
    dtostrf(temp, 1, 1, temperatura);
    sprintf(query, INSERT_DATA, id, temp);
    // Execute the query
    cur_mem->execute(query);
    // Note: since there are no results, we do not need to read any data
    // Deleting the cursor also frees up memory used
    delete cur_mem;
  } else {
    EnviaDados(id, t);
  }
  conn.close();
}

void VerificaWiFi() {
  if (WiFi.status() != WL_CONNECTED) {
    delay(5000);
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Sem conexao");
    WiFi.disconnect();
    delay(1000);
    WiFi.begin(ssid, pass);
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Conectando");
    lcd.setCursor(0, 1);
    lcd.print("a rede");
    while (WiFi.status() != WL_CONNECTED) {
      digitalWrite(1, HIGH);
      delay(100);
      digitalWrite(1, LOW);
      delay(500);
    }
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Conectado");
    digitalWrite(1, HIGH);
    delay(100);
    digitalWrite(1, LOW);
    delay(100);
    digitalWrite(1, HIGH);
    delay(100);
    digitalWrite(1, LOW);
    delay(100);
    digitalWrite(1, HIGH);
    delay(100);
    digitalWrite(1, LOW);
    delay(100);
  }
}
void enviaMensagem() {
  String statual = String(tatual);
  String mensagem = "Alerta de temperatura:   " + statual + "°C \nAcesse o link para obter mais informações \nhttps://.repl.co/";
  secured_client.setTrustAnchors(&cert);  // Add root certificate for api.telegram.org
  configTime(0, 0, "pool.ntp.org");       // get UTC time via NTP
  time_t now = time(nullptr);
  while (now < 24 * 3600) {
    delay(100);
    now = time(nullptr);
  }
  bot.sendMessage(CHAT_ID, mensagem, "");
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Mensagem enviada");
  delay(2000);
}