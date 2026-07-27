(() => {
  "use strict";
  const job = document.body.dataset.job;
  const message = document.getElementById("message");
  const progress = document.getElementById("progress");
  const summaryPage = document.getElementById("summary-page");
  const summaryAnalyzed = document.getElementById("summary-analyzed");
  const summaryAdded = document.getElementById("summary-added");
  const summaryIgnored = document.getElementById("summary-ignored");
  const lastWarning = document.getElementById("last-warning");

  function updateSummary(entries) {
    entries.forEach((entry) => {
      const page = entry.match(/Página\s+(\d+)\s+de\s+(\d+).*?(\d+)\s+registros examinados.*?(\d+)\s+atendimentos/i);
      if (page) {
        summaryPage.textContent = page[1] + " de " + page[2];
        summaryAnalyzed.textContent = page[3];
        summaryAdded.textContent = page[4];
      }
      const ignored = entry.match(/(\d+)\s+itens ignorados.*?último aviso:\s*(.+?)(?:\.\s*Seguindo|$)/i);
      if (ignored) {
        summaryIgnored.textContent = ignored[1];
        lastWarning.textContent = "Último aviso: " + ignored[2];
        lastWarning.hidden = false;
      }
      const row = entry.match(/Resumo\s+(\d+)\s+consultado;\s*(\d+)\s+atendimentos/i);
      if (row) {
        summaryAnalyzed.textContent = row[1];
        summaryAdded.textContent = row[2];
      }
    });
  }

  async function poll() {
    try {
      const response = await fetch(
        "/api/trabalho/" + encodeURIComponent(job),
        {cache: "no-store", credentials: "same-origin"},
      );
      const data = await response.json();
      const entries = Array.isArray(data.progress) ? data.progress.map(String) : [];
      message.textContent = data.message;
      progress.replaceChildren(...entries.map((entry) => {
        const item = document.createElement("li");
        item.textContent = entry;
        return item;
      }));
      updateSummary(entries);
      if (data.status === "DONE" && data.result && data.result.redirect) {
        location.href = data.result.redirect;
        return;
      }
      if (data.status === "ERROR") {
        document.body.classList.add("error");
        message.setAttribute("role", "alert");
        return;
      }
      setTimeout(poll, 800);
    } catch {
      message.textContent = "Não foi possível atualizar o progresso local. Tentando novamente…";
      setTimeout(poll, 1200);
    }
  }
  poll();
})();
