(() => {
  const command = document.getElementById("install-code");
  const commandBlock = command ? command.closest("pre") : null;
  if (!command || !commandBlock) return;

  const button = document.createElement("button");
  const label = document.createElement("span");
  button.className = "copy-button";
  button.type = "button";
  button.dataset.copyTarget = command.id;
  label.textContent = "Copy command";
  button.append(label);
  commandBlock.after(button);

  const status = document.querySelector("[data-copy-status]");
  const originalStatus = status ? status.textContent : "";
  let resetTimer;

  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;

    window.clearTimeout(resetTimer);
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      if (status) status.textContent = "Command copied to clipboard.";
    } catch (_error) {
      if (status) status.textContent = "Copy unavailable; command selected.";
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.removeAllRanges();
      selection.addRange(range);
    }

    window.clearTimeout(resetTimer);
    resetTimer = window.setTimeout(() => {
      if (status) status.textContent = originalStatus;
    }, 1800);
  });
})();
