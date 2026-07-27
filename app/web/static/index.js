(() => {
  "use strict";

  const competence = document.getElementById("active-competence");
  if (!competence) return;

  const mirrors = [...document.querySelectorAll(".competence-mirror")];
  const competenceError = document.getElementById("competence-error");
  const competenceDescription = document.getElementById("competence-description");
  const pecCompetenceLabel = document.getElementById("pec-competence-label");
  const months = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
  ];

  const spreadsheetForm = document.getElementById("spreadsheet-form");
  const fileInput = document.getElementById("spreadsheet-file");
  const uploadZone = document.getElementById("upload-zone");
  const fileSelection = document.getElementById("file-selection");
  const fileName = document.getElementById("file-name");
  const fileMeta = document.getElementById("file-meta");
  const fileError = document.getElementById("file-error");
  const removeFile = document.getElementById("remove-file");
  const importSubmit = document.getElementById("import-submit");

  const pecForm = document.getElementById("pec-search-form");
  const startDate = document.getElementById("start-date");
  const endDate = document.getElementById("end-date");
  const startError = document.getElementById("start-date-error");
  const endError = document.getElementById("end-date-error");
  const periodWarning = document.getElementById("period-warning");
  const pecFormError = document.getElementById("pec-form-error");
  const pecSubmit = document.getElementById("pec-submit");
  const pecRunning = document.body.dataset.pecRunning === "true";
  const acceptedExtensions = new Set(["xlsx", "csv", "tsv"]);
  let spreadsheetSubmitting = false;
  let pecSubmitting = false;

  function competenceState(showError = true) {
    const value = competence.value;
    let message = "";
    if (!/^\d{6}$/.test(value)) {
      message = "A competência deve possuir seis números no formato AAAAMM.";
    } else {
      const month = Number(value.slice(4));
      if (month < 1 || month > 12) message = "O mês informado na competência é inválido.";
    }
    if (showError || !message) {
      competence.setAttribute("aria-invalid", showError && message ? "true" : "false");
    }
    if (showError) competenceError.textContent = message;
    else if (!message) competenceError.textContent = "";
    if (!message) {
      const month = Number(value.slice(4));
      competenceDescription.textContent = months[month - 1] + " de " + value.slice(0, 4);
      pecCompetenceLabel.textContent = value;
      mirrors.forEach((field) => { field.value = value; });
    } else {
      competenceDescription.textContent = "—";
      pecCompetenceLabel.textContent = "—";
      mirrors.forEach((field) => { field.value = ""; });
    }
    return !message;
  }

  function selectedFile() {
    return fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
  }

  function fileState(showError = true) {
    const file = selectedFile();
    let message = "";
    if (!file) {
      message = "Selecione um arquivo XLSX, CSV ou TSV.";
    } else {
      const extension = (file.name.split(".").pop() || "").toLowerCase();
      if (!acceptedExtensions.has(extension)) message = "Selecione um arquivo XLSX, CSV ou TSV.";
    }
    if (showError || !message) {
      fileInput.setAttribute("aria-invalid", showError && message ? "true" : "false");
    }
    if (showError) fileError.textContent = message;
    else if (!message) fileError.textContent = "";
    fileSelection.hidden = !file || Boolean(message);
    uploadZone.hidden = Boolean(file) && !message;
    if (file && !message) {
      const extension = file.name.split(".").pop().toUpperCase();
      const size = file.size < 1024 * 1024
        ? Math.max(1, Math.round(file.size / 1024)) + " KB"
        : (file.size / (1024 * 1024)).toFixed(1) + " MB";
      fileName.textContent = file.name;
      fileMeta.textContent = extension + " · " + size;
    }
    return !message;
  }

  function periodState(showError = true) {
    let startMessage = "";
    let endMessage = "";
    if (!startDate.value) startMessage = "Informe a data inicial.";
    if (!endDate.value) endMessage = "Informe a data final.";
    if (!startMessage && !endMessage && endDate.value < startDate.value) {
      endMessage = "A data final não pode ser anterior à data inicial.";
    }
    if (showError || !startMessage) {
      startDate.setAttribute("aria-invalid", showError && startMessage ? "true" : "false");
    }
    if (showError || !endMessage) {
      endDate.setAttribute("aria-invalid", showError && endMessage ? "true" : "false");
    }
    if (showError) {
      startError.textContent = startMessage;
      endError.textContent = endMessage;
    } else {
      if (!startMessage) startError.textContent = "";
      if (!endMessage) endError.textContent = "";
    }

    periodWarning.textContent = "";
    if (!startMessage && !endMessage && competenceState(false)) {
      const expected = competence.value;
      const startCompetence = startDate.value.slice(0, 7).replace("-", "");
      const endCompetence = endDate.value.slice(0, 7).replace("-", "");
      if (startCompetence !== expected || endCompetence !== expected) {
        periodWarning.textContent = "Atenção: uma ou mais datas não pertencem à competência ativa.";
      }
    }
    return !startMessage && !endMessage;
  }

  function updateButtons() {
    const validCompetence = competenceState(false);
    importSubmit.disabled = spreadsheetSubmitting || !validCompetence || !fileState(false);
    pecSubmit.disabled = pecRunning || pecSubmitting || !validCompetence || !periodState(false);
  }

  competence.addEventListener("input", () => {
    competence.value = competence.value.replace(/\D/g, "").slice(0, 6);
    competenceState(competence.value.length === 6);
    periodState(false);
    updateButtons();
  });
  competence.addEventListener("blur", () => {
    competenceState(true);
    updateButtons();
  });

  fileInput.addEventListener("change", () => {
    fileState(true);
    updateButtons();
  });
  removeFile.addEventListener("click", () => {
    fileInput.value = "";
    fileSelection.hidden = true;
    uploadZone.hidden = false;
    fileError.textContent = "";
    updateButtons();
    uploadZone.focus();
  });
  uploadZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  ["dragenter", "dragover"].forEach((name) => uploadZone.addEventListener(name, (event) => {
    event.preventDefault();
    uploadZone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((name) => uploadZone.addEventListener(name, (event) => {
    event.preventDefault();
    uploadZone.classList.remove("is-dragging");
  }));
  uploadZone.addEventListener("drop", (event) => {
    if (event.dataTransfer.files.length) {
      const transfer = new DataTransfer();
      transfer.items.add(event.dataTransfer.files[0]);
      fileInput.files = transfer.files;
      fileState(true);
      updateButtons();
    }
  });

  [startDate, endDate].forEach((field) => field.addEventListener("change", () => {
    periodState(true);
    updateButtons();
  }));

  spreadsheetForm.addEventListener("submit", (event) => {
    if (spreadsheetSubmitting || !competenceState(true) || !fileState(true)) {
      event.preventDefault();
      const target = competence.getAttribute("aria-invalid") === "true" ? competence : fileInput;
      target.focus();
      return;
    }
    spreadsheetSubmitting = true;
    spreadsheetForm.setAttribute("aria-busy", "true");
    importSubmit.disabled = true;
    importSubmit.textContent = "Importando...";
  });

  pecForm.addEventListener("submit", (event) => {
    const valid = competenceState(true) && periodState(true);
    if (pecRunning || pecSubmitting || !valid) {
      event.preventDefault();
      if (pecRunning) pecFormError.textContent = "Já existe uma busca em andamento.";
      const target = competence.getAttribute("aria-invalid") === "true"
        ? competence
        : startDate.getAttribute("aria-invalid") === "true" ? startDate : endDate;
      target.focus();
      return;
    }
    pecSubmitting = true;
    pecForm.setAttribute("aria-busy", "true");
    pecSubmit.disabled = true;
    pecSubmit.textContent = "Iniciando busca...";
  });

  competenceState(false);
  fileState(false);
  periodState(false);
  updateButtons();
})();
