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
  let progressCursor = 0;

  function updateSummary(entries) {
    entries.forEach((entry) => {
      const page = entry.match(/Página\s+(\d+)\s+de\s+(\d+).*?(\d+)\s+registros examinados.*?(\d+)\s+atendimentos/i);
      if (page) {
        summaryPage.textContent = page[1] + " de " + page[2];
        summaryAnalyzed.textContent = page[3];
        summaryAdded.textContent = page[4];
      }
      const ignored = entry.match(/^(\d+)\s+it(?:em|ens)\s+ignorados?\.\s*(.+)$/i);
      if (ignored) {
        summaryIgnored.textContent = ignored[1];
        lastWarning.textContent = ignored[2];
        lastWarning.hidden = false;
      }
      const row = entry.match(/^(\d+)\s+registros examinados e\s+(\d+)\s+atendimentos/i);
      if (row) {
        summaryAnalyzed.textContent = row[1];
        summaryAdded.textContent = row[2];
      }
    });
  }

  function showError(text) {
    document.body.classList.add("error");
    message.setAttribute("role", "alert");
    message.textContent = text;
  }

  async function poll() {
    try {
      const response = await fetch(
        "/api/trabalho/" + encodeURIComponent(job) + "?after=" + progressCursor,
        {cache: "no-store", credentials: "same-origin"},
      );
      if (response.status === 403) {
        showError("A sessão local do importador expirou. Reabra o importador para acessá-lo.");
        return;
      }
      if (response.status === 404) {
        showError("Esta operação já não está disponível nesta execução do importador.");
        return;
      }
      if (!response.ok) {
        throw new Error("Falha na consulta de progresso");
      }
      const data = await response.json();
      const entries = Array.isArray(data.progress) ? data.progress.map(String) : [];
      message.textContent = data.message;
      const additions = document.createDocumentFragment();
      entries.forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = entry;
        additions.appendChild(item);
      });
      progress.appendChild(additions);
      progressCursor += entries.length;
      updateSummary(entries);
      if (data.status === "DONE" && data.result && data.result.redirect) {
        location.href = data.result.redirect;
        return;
      }
      if (data.status === "ERROR") {
        showError(data.message);
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
