(() => {
  const cards = [...document.querySelectorAll(".question-card")];
  if (!cards.length) return;
  const state = JSON.parse(document.getElementById("exam-state").textContent);
  const storageKey = `workspace:${state.attempt}:${state.section}`;
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
  } catch {}
  const persist = () => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(saved));
    } catch {}
  };
  const select = document.getElementById("source-page");
  function source(id) {
    if (!select || ![...select.options].some((o) => o.value === id)) return;
    select.value = id;
    document.querySelectorAll(".source-image").forEach((img) => {
      img.hidden = img.dataset.sourceId !== id;
    });
    const img = document.querySelector(".source-image:not([hidden])");
    const scrollToContent = () => {
      document.querySelector(".source-scroll").scrollTop =
        img.clientHeight * 0.18;
    };
    if (img.complete) scrollToContent();
    else img.addEventListener("load", scrollToContent, { once: true });
  }
  select?.addEventListener("change", () => source(select.value));
  for (const [id, step] of [
    ["source-prev", -1],
    ["source-next", 1],
  ]) {
    document.getElementById(id)?.addEventListener("click", () => {
      const index = Math.max(
        0,
        Math.min(select.options.length - 1, select.selectedIndex + step),
      );
      source(select.options[index].value);
    });
  }
  const audio = document.getElementById("listening-audio");
  if (audio?.controls) {
    const speed = document.createElement("select");
    speed.setAttribute("aria-label", "Скорость аудио");
    [0.75, 1, 1.25, 1.5].forEach((rate) => {
      const option = document.createElement("option");
      option.value = rate;
      option.textContent = rate + "×";
      option.selected = rate === 1;
      speed.append(option);
    });
    speed.addEventListener("change", () => {
      audio.playbackRate = Number(speed.value);
    });
    audio.after(speed);
  }
  const parts = [...new Set(cards.map((c) => c.dataset.part))];
  function part(value, target) {
    cards.forEach((c) => {
      c.hidden = c.dataset.part !== value;
    });
    document.querySelectorAll("[data-part-tab]").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.partTab === value));
      b.tabIndex = b.dataset.partTab === value ? 0 : -1;
    });
    const card = target || cards.find((c) => c.dataset.part === value);
    if (card?.dataset.material) source(card.dataset.material);
    saved.part = value;
    persist();
  }
  parts.forEach((value) => {
    const button = document.createElement("button");
    button.type = "button";
    button.role = "tab";
    button.dataset.partTab = value;
    button.textContent = `${state.kind === "reading" ? "Passage" : state.kind === "writing" ? "Task" : "Part"} ${value}`;
    button.addEventListener("click", () => part(value));
    button.addEventListener("keydown", (e) => {
      if (!["ArrowRight", "ArrowLeft"].includes(e.key)) return;
      e.preventDefault();
      const i =
        (parts.indexOf(value) +
          (e.key === "ArrowRight" ? 1 : -1) +
          parts.length) %
        parts.length;
      part(parts[i]);
      document.querySelector(`[data-part-tab="${parts[i]}"]`).focus();
    });
    document.getElementById("part-tabs").append(button);
  });
  cards.forEach((card) => {
    const id = card.dataset.question;
    const link = document.querySelector(
      `.question-nav a[href="#question-${id}"]`,
    );
    const flag = document.createElement("button");
    flag.type = "button";
    flag.className = "flag-question";
    flag.textContent = "⚑ Отметить";
    const setFlag = (value) => {
      flag.setAttribute("aria-pressed", String(value));
      card.classList.toggle("flagged", value);
      link?.classList.toggle("flagged", value);
    };
    setFlag(!!saved.flags?.[id]);
    flag.addEventListener("click", () => {
      saved.flags ||= {};
      saved.flags[id] = !saved.flags[id];
      setFlag(saved.flags[id]);
      persist();
    });
    card.prepend(flag);
    link?.addEventListener("click", () => {
      part(card.dataset.part, card);
      document
        .querySelectorAll(".active-question")
        .forEach((x) => x.classList.remove("active-question"));
      link.classList.add("active-question");
    });
  });
  part(parts.includes(saved.part) ? saved.part : parts[0]);
  document.getElementById("test-focus").addEventListener("click", (e) => {
    const active = document.body.classList.toggle("test-focus");
    e.currentTarget.setAttribute("aria-pressed", String(active));
    e.currentTarget.textContent = active ? "⛶ Обычный вид" : "⛶ Фокус";
  });
  document
    .getElementById("test-font")
    .addEventListener("click", () =>
      document.body.classList.toggle("test-large"),
    );
  document
    .getElementById("test-export")
    .addEventListener("click", () => window.print());
  const notes = document.getElementById("test-notes");
  notes.value = saved.notes || "";
  notes.addEventListener("input", () => {
    saved.notes = notes.value;
    persist();
  });
  document.getElementById("test-notes-toggle").addEventListener("click", () => {
    const p = document.getElementById("test-notes-panel");
    p.hidden = !p.hidden;
    if (!p.hidden) notes.focus();
  });
  let selection;
  document.addEventListener("selectionchange", () => {
    const s = window.getSelection();
    if (
      s.rangeCount &&
      s.toString() &&
      s.anchorNode.parentElement.closest(".question-card,.reading-passage")
    )
      selection = s.getRangeAt(0).cloneRange();
  });
  document.getElementById("test-highlight").addEventListener("click", () => {
    if (!selection) return;
    try {
      selection.surroundContents(document.createElement("mark"));
    } catch {}
    selection = null;
  });
})();
