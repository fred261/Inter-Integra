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
