/* =============================================================================
   trellis.js — small client helpers (no framework, no build step)
============================================================================= */

(function () {
  "use strict";

  // ---------------------------------------------------------------- mention

  // <div data-mention-input
  //      data-types="Equipment,System"
  //      data-mode="single|multi"
  //      data-name="pk__betrifft"
  //      data-search-url="/search"
  //      data-create-url="/quick-create">
  //    <div class="mention-chips"></div>
  //    <input class="mention-input" type="text" autocomplete="off">
  //    <div class="mention-popup"></div>
  // </div>

  class MentionInput {
    constructor(root) {
      this.root        = root;
      this.types       = (root.dataset.types || "").split(",").filter(Boolean);
      this.mode        = root.dataset.mode || "single";
      this.fieldName   = root.dataset.name || "ref";
      this.searchUrl   = root.dataset.searchUrl || "/search";
      this.createUrl   = root.dataset.createUrl || "/quick-create";

      this.input  = root.querySelector(".mention-input");
      this.chips  = root.querySelector(".mention-chips");
      this.popup  = root.querySelector(".mention-popup");

      this.activeIndex = -1;
      this.lastFetched = "";
      this.bind();
    }

    bind() {
      this.input.addEventListener("input",   () => this.onInput());
      this.input.addEventListener("keydown", (e) => this.onKey(e));
      this.input.addEventListener("focus",   () => { if (this.input.value) this.onInput(); });
      // Delay close so click on popup is still registered
      this.input.addEventListener("blur",    () => setTimeout(() => this.close(), 120));

      this.popup.addEventListener("mousedown", (e) => {
        const item = e.target.closest(".mention-item");
        if (item) { e.preventDefault(); this.select(item); }
      });

      // Existing chips: bind their remove buttons
      this.chips.querySelectorAll(".chip-remove").forEach((btn) => {
        btn.addEventListener("click", () => btn.closest(".mention-chip").remove());
      });
    }

    async onInput() {
      const q = this.input.value.trim();
      if (q.length === 0) { this.close(); return; }
      const url = `${this.searchUrl}?q=${encodeURIComponent(q)}` +
                  `&types=${encodeURIComponent(this.types.join(","))}`;
      this.lastFetched = q;
      let res;
      try { res = await fetch(url, { headers: { "Accept": "text/html" } }); }
      catch { return; }
      if (!res.ok) return;
      // Race guard: only apply the response if still the latest query
      if (this.lastFetched !== q) return;
      const html = await res.text();
      this.popup.innerHTML = html;
      this.popup.classList.add("is-open");
      this.activeIndex = 0;
      this.highlight();
    }

    onKey(ev) {
      if (!this.popup.classList.contains("is-open")) return;
      const items = this.popup.querySelectorAll(".mention-item");
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        this.activeIndex = Math.min(items.length - 1, this.activeIndex + 1);
        this.highlight();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        this.activeIndex = Math.max(0, this.activeIndex - 1);
        this.highlight();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        const item = items[this.activeIndex];
        if (item) this.select(item);
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        this.close();
      }
    }

    highlight() {
      this.popup.querySelectorAll(".mention-item").forEach((el, i) => {
        el.classList.toggle("is-active", i === this.activeIndex);
        if (i === this.activeIndex) el.scrollIntoView({ block: "nearest" });
      });
    }

    async select(item) {
      let ref, name;
      if (item.dataset.create) {
        const type = item.dataset.create;
        const wantName = item.dataset.name;
        const fd = new FormData();
        fd.set("type_name", type);
        fd.set("name", wantName);
        let res;
        try { res = await fetch(this.createUrl, { method: "POST", body: fd }); }
        catch { return; }
        if (!res.ok) return;
        const data = await res.json();
        ref = data.ref;
        name = data.name;
      } else {
        ref = item.dataset.ref;
        name = item.dataset.name;
      }
      this.addChip(ref, name);
      this.input.value = "";
      this.close();
    }

    addChip(ref, name) {
      if (this.mode === "single") this.chips.innerHTML = "";
      const existing = this.chips.querySelector(`input[value="${cssEscape(ref)}"]`);
      if (existing) return;
      const chip = document.createElement("span");
      chip.className = "mention-chip";
      const nameEl = document.createElement("span");
      nameEl.className = "chip-name";
      nameEl.textContent = name;
      const hidden = document.createElement("input");
      hidden.type  = "hidden";
      hidden.name  = this.fieldName;
      hidden.value = ref;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "chip-remove";
      rm.textContent = "✕";
      rm.addEventListener("click", () => chip.remove());
      chip.appendChild(nameEl);
      chip.appendChild(hidden);
      chip.appendChild(rm);
      this.chips.appendChild(chip);
    }

    close() {
      this.popup.classList.remove("is-open");
      this.popup.innerHTML = "";
      this.activeIndex = -1;
    }
  }

  function cssEscape(s) {
    // Minimal escape for the input-value selector. We never accept user-controlled
    // refs without going through the server, so this is just defensive.
    return String(s).replace(/["\\]/g, "\\$&");
  }

  function initMentionInputs(root) {
    (root || document).querySelectorAll(
      "[data-mention-input]:not([data-mention-init])"
    ).forEach((el) => {
      new MentionInput(el);
      el.dataset.mentionInit = "1";
    });
  }

  // ---------------------------------------------------------------- bootstrap

  document.addEventListener("DOMContentLoaded", () => initMentionInputs());
  // HTMX swaps in new HTML — re-bind any mention inputs inside the new fragment.
  document.body.addEventListener("htmx:afterSettle", (ev) => {
    initMentionInputs(ev.detail && ev.detail.target);
  });
})();
