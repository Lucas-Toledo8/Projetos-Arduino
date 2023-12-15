<?php
// Conectar ao banco de dados
$servername = "0.0.0.0";
$username = "usuario";
$password = "senha";
$dbname = "database";

$conn = new mysqli($servername, $username, $password, $dbname);

if ($conn->connect_error) {
  die("Connection failed: " . $conn->connect_error);
}

// Obter a temperatura atual do banco de dados
$sql = "SELECT Temperatura FROM Temperatura ORDER BY DATA_HORA DESC LIMIT 1";
$result = $conn->query($sql);

if ($result->num_rows > 0) {
  $row = $result->fetch_assoc();
  $current_temp = $row['Temperatura'];
} else {
  $current_temp = "N/A";
}

// Obter a previsão do tempo do banco de dados
$sql = "SELECT Temperatura, DATA_HORA FROM Temperatura WHERE DATA_HORA > NOW() ORDER BY DATA_HORA ASC LIMIT 5";
$result = $conn->query($sql);

$forecast_temp = array();
$forecast_date = array();

if ($result->num_rows > 0) {
  while($row = $result->fetch_assoc()) {
    array_push($forecast_temp, $row['Temperatura']);
    array_push($forecast_date, $row['DATA_HORA']);
  }
}

// Obter o histórico das temperaturas do banco de dados
$sql = "SELECT * FROM Temperatura ORDER BY DATA_HORA DESC LIMIT 10";
$result = $conn->query($sql);

$history_temp = array();
$history_date = array();

if ($result->num_rows > 0) {
  while($row = $result->fetch_assoc()) {
    array_push($history_temp, $row['Temperatura']);
    array_push($history_date, $row['DATA_HORA']);
  }
}

$conn->close();
?>

<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>SECITECI - ETEEPT Cuiabá</title>
  <!-- Incluir o Bootstrap 5 para estilizar o site -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css">
</head>
<body>
  <div class="container">
    <h1>SECITECI - ETEEP</h1>
    <div class="row">
      <div class="col-md-6">
        <h2>Temperatura Atual</h2>
        <h3>🌡 <?php echo $current_temp; ?> °C.</h3>
      </div>
      <div class="col-md-6">
    
        <table class="table table-striped">
          <thead>
            
          </thead>
          <tbody>
            <?php for ($i = 0; $i < count($forecast_temp); $i++) { ?>
            <tr>
              <td><?php echo $forecast_date[$i]; ?></td>
              <td><?php echo $forecast_temp[$i]; ?> °C</td>
            </tr>
            <?php } ?>
          </tbody>
        </table>
      </div>
    </div>
    <div class="row">
      <div class="col-md-12">
        <h2>Histórico das Temperaturas</h2>
        <table class="table table-striped">
          <thead>
            <tr>
              <th>Data</th>
              <th>Temperatura</th>
            </tr>
          </thead>
          <tbody>
            <?php for ($i = 0; $i < count($history_temp); $i++) { ?>
            <tr>
              <td>🗓 <?php echo $history_date[$i]; ?></td>
              <td>🌡 <?php echo $history_temp[$i]; ?> °C</td>
            </tr>
            <?php } ?>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>

