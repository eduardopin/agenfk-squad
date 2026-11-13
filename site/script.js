// InstallSnippet copy button (Pin Open 1.0.0): rest "Copy", success "Copied"
// for 1.8 s, failure "Select text" with the command selected. The "$ " prompt
// is never copied. Without JavaScript the command stays selectable as text.
(() => {
  const status = document.querySelector("[data-copy-status]");
  const REST = "Copy";
  const HOLD = 1800;

  const commandOf = (code) => {
    const clone = code.cloneNode(true);
    clone.querySelectorAll(".prompt").forEach((prompt) => prompt.remove());
    return clone.textContent.trim();
  };

  const select = (code) => {
    const selection = window.getSelection();
    const range = document.createRange();
    const prompt = code.querySelector(".prompt");
    range.selectNodeContents(code);
    if (prompt) range.setStartAfter(prompt);
    selection.removeAllRanges();
    selection.addRange(range);
  };

  const announce = (text) => {
    if (!status) return;
    status.textContent = "";
    window.setTimeout(() => { status.textContent = text; }, 50);
  };

  document.querySelectorAll("[data-install]").forEach((snippet) => {
    const bar = snippet.querySelector(".po-install__bar");
    const code = snippet.querySelector("pre code");
    if (!bar || !code) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "po-copy";
    button.textContent = REST;
    button.setAttribute("aria-label", "Copy the install command");
    bar.append(button);

    let timer;
    const reset = () => {
      button.textContent = REST;
      delete button.dataset.state;
    };

    button.addEventListener("click", async () => {
      window.clearTimeout(timer);
      try {
        if (!navigator.clipboard) throw new Error("clipboard unavailable");
        await navigator.clipboard.writeText(commandOf(code));
        button.textContent = "Copied";
        button.dataset.state = "copied";
        announce("Command copied.");
      } catch (_error) {
        select(code);
        button.textContent = "Select text";
        button.dataset.state = "failed";
        announce("Copy unavailable; command selected.");
      }
      timer = window.setTimeout(reset, HOLD);
    });
  });
})();
