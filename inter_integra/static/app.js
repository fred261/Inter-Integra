// Soma ao vivo do total selecionado na tela de folha.
(function () {
  var form = document.getElementById("form-folha");
  var alvo = document.getElementById("total-folha");
  if (!form || !alvo) return;
  function centavos(txt) {
    var s = txt.replace(/R\$|\s/g, "");
    if (s.indexOf(",") >= 0) s = s.replace(/\./g, "").replace(",", ".");
    var n = parseFloat(s);
    return isNaN(n) ? 0 : Math.round(n * 100);
  }
  function somar() {
    var total = 0;
    form.querySelectorAll("tbody tr").forEach(function (tr) {
      var marcado = tr.querySelector("[data-linha]").checked;
      if (marcado) total += centavos(tr.querySelector("[data-valor]").value);
    });
    alvo.textContent = (total / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }
  form.addEventListener("input", somar);
  form.addEventListener("change", somar);
  somar();
})();

// Tela Copia e Cola: ao colar o código, mostra o valor e o recebedor lidos pelo servidor.
(function () {
  var campo = document.getElementById("codigo");
  var previa = document.getElementById("previa");
  var valor = document.getElementById("valor");
  if (!campo || !previa) return;
  var token = campo.form.querySelector("[name=csrf_token]").value;
  var timer = null;
  function mostrar(classe, texto) {
    previa.hidden = false;
    previa.className = "alerta " + classe;
    previa.textContent = texto;  // textContent: nada vindo do código vira HTML
  }
  function ler() {
    var codigo = campo.value.trim();
    if (!codigo) { previa.hidden = true; return; }
    var corpo = new URLSearchParams({ csrf_token: token, codigo: codigo });
    fetch("/caju/ler", { method: "POST", body: corpo, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { mostrar("alerta-erro", j.erro); return; }
        var quem = j.recebedor ? "Recebedor: " + j.recebedor + " (lido do código, não verificado). " : "";
        if (j.valor) {
          valor.value = j.valor;
          mostrar("alerta-info", quem + "Valor do código: R$ " + j.valor);
        } else {
          mostrar("alerta-erro", quem + "Este código não traz o valor (Pix dinâmico). Informe o valor abaixo.");
        }
      })
      .catch(function () { previa.hidden = true; });
  }
  campo.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(ler, 300); });
  if (campo.value) ler();
})();
