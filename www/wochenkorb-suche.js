// Suchfeld, das schon beim Tippen sucht: schreibt den Text nach einer kurzen Pause in einen Text-Helfer.
// Das Standard-Textfeld von Home Assistant gibt den Text erst mit Enter oder beim Verlassen weiter.
//
// type: custom:wochenkorb-suche
// entity: input_text.rezept_suche     Text-Helfer, auf den die Suche hört
// label: Rezept suchen                Hinweistext im leeren Feld
// delay: 300                          Pause nach dem letzten Tastendruck in Millisekunden
// min_length: 0                       erst ab so vielen Zeichen suchen (leeren geht immer sofort)
class WochenkorbSuche extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("entity fehlt");
    this._config = { label: "Suchen", delay: 300, min_length: 0, ...config };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._input) this._build();
    const state = hass.states[this._config.entity];
    const value = state && !["unknown", "unavailable"].includes(state.state) ? state.state : "";
    // Solange getippt wird, bestimmt das Feld; sonst folgt es dem Helfer (zum Beispiel nach „Suche beenden“)
    const typing = this.shadowRoot.activeElement === this._input || this._timer;
    if (!typing && this._input.value !== value) this._input.value = value;
    this._clear.style.visibility = this._input.value ? "visible" : "hidden";
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { display: flex; align-items: center; gap: 10px; padding: 0 8px 0 16px; height: 52px; box-sizing: border-box; }
        ha-icon { color: var(--primary-color); --mdc-icon-size: 22px; flex: none; }
        input { flex: 1; min-width: 0; height: 100%; border: none; outline: none; background: none;
                color: var(--primary-text-color); font: inherit; font-size: 16px; }
        input::placeholder { color: var(--secondary-text-color); opacity: 1; }
        /* Das eigene Leeren-Kreuz des Browsers ausblenden, die Karte hat ein eigenes */
        input::-webkit-search-cancel-button, input::-webkit-search-decoration { -webkit-appearance: none; display: none; }
        button { flex: none; width: 36px; height: 36px; border: none; border-radius: 50%; background: none; cursor: pointer;
                 color: var(--secondary-text-color); display: flex; align-items: center; justify-content: center; padding: 0; }
        button:hover { background: rgba(127, 127, 127, 0.18); }
        button ha-icon { color: inherit; --mdc-icon-size: 20px; }
      </style>
      <ha-card>
        <ha-icon icon="mdi:magnify"></ha-icon>
        <input type="search" enterkeyhint="search" autocomplete="off" autocapitalize="off" spellcheck="false">
        <button type="button" aria-label="Suche leeren"><ha-icon icon="mdi:close"></ha-icon></button>
      </ha-card>`;
    this._input = root.querySelector("input");
    this._clear = root.querySelector("button");
    this._input.placeholder = this._config.label;
    this._input.setAttribute("aria-label", this._config.label);
    this._input.addEventListener("input", () => this._changed(false));
    this._input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        this._changed(true);
        this._input.blur();
      }
    });
    this._clear.addEventListener("click", () => {
      this._input.value = "";
      this._changed(true);
      this._input.focus();
    });
  }

  _changed(now) {
    clearTimeout(this._timer);
    this._timer = null;
    const text = this._input.value.trim();
    this._clear.style.visibility = this._input.value ? "visible" : "hidden";
    // Zu kurze Eingaben lösen noch keine Suche aus; ein geleertes Feld beendet sie sofort
    if (!now && text && text.length < this._config.min_length) return;
    if (now || !text) return this._send(text);
    this._timer = setTimeout(() => {
      this._timer = null;
      this._send(text);
    }, this._config.delay);
  }

  _send(text) {
    const state = this._hass.states[this._config.entity];
    if (state && state.state === text) return;
    this._hass.callService("input_text", "set_value", { entity_id: this._config.entity, value: text });
  }

  getCardSize() {
    return 1;
  }
}

if (!customElements.get("wochenkorb-suche")) {
  customElements.define("wochenkorb-suche", WochenkorbSuche);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "wochenkorb-suche", name: "Wochenkorb Suche", description: "Suchfeld, das beim Tippen sucht" });
}
