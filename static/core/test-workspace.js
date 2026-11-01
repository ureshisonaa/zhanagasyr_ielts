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
    document.querySelectorAll("[data-test-part]").forEach((group) => {
      group.hidden = group.dataset.testPart !== value;
    });
    document.querySelectorAll("[data-passage-part]").forEach((passage) => {
      passage.hidden = passage.dataset.passagePart !== value;
    });
    if (saved.part !== value)
      document.querySelector(".passage-pane")?.scrollTo(0, 0);
    document.querySelectorAll("[data-part-tab]").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.partTab === value));
      b.tabIndex = b.dataset.partTab === value ? 0 : -1;
    });
    const card = target || cards.find((c) => c.dataset.part === value);
    if (card?.dataset.material) source(card.dataset.material);
    if (audio && saved.part !== value) {
      audio.pause();
      audio.removeAttribute("src");
      audio.hidden = true;
      audio.load();
      const play = document.getElementById("play-listening");
      if (play) {
        play.disabled = false;
        play.dataset.part = value;
        play.textContent = "▶ Listen · Part " + value;
      }
      document.getElementById("audio-status").textContent = "Нажмите «Слушать», чтобы загрузить аудио этой части.";
    }
    if (audio) {
      const play = document.getElementById("play-listening");
      if (play) play.dataset.part = value;
    }
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
    link?.addEventListener("click", () => {
      part(card.dataset.part, card);
      document
        .querySelectorAll(".active-question")
        .forEach((x) => x.classList.remove("active-question"));
      link.classList.add("active-question");
    });
  });
  // Matching keeps a real select as the canonical value for autosave and keyboard access.
  let selectedToken = null;
  function emit(select) {
    select.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function feedback(select) {
    const drop = document.querySelector(
      `[data-drop-for="${select.dataset.answer}"]`,
    );
    if (!drop) return;
    drop.textContent = select.value
      ? select.selectedOptions[0].textContent
      : "Drag your answer here";
    drop.classList.toggle("has-answer", !!select.value);
    drop.draggable = !!select.value;
  }
  function applyToken(drop, token) {
    const control = drop.closest(".matching-control");
    const select = control.querySelector("select");
    if (
      !token ||
      token.group !== control.dataset.matchGroup ||
      ![...select.options].some((o) => o.value === token.value)
    )
      return;
    if (token.source && token.source !== select.dataset.answer) {
      const previous = document.getElementById("answer-" + token.source);
      if (previous) {
        previous.value = "";
        feedback(previous);
        emit(previous);
      }
    }
    select.value = token.value;
    feedback(select);
    emit(select);
    selectedToken = null;
    document
      .querySelectorAll(".answer-token")
      .forEach((t) => t.setAttribute("aria-pressed", "false"));
  }
  document.querySelectorAll(".answer-token").forEach((token) => {
    const data = () => ({
      value: token.dataset.tokenValue,
      group: token.dataset.tokenGroup,
    });
    token.addEventListener("dragstart", (e) => {
      selectedToken = data();
      e.dataTransfer.setData("application/json", JSON.stringify(selectedToken));
      e.dataTransfer.effectAllowed = "copyMove";
    });
  });
  document.querySelectorAll(".matching-control select").forEach((select) => {
    feedback(select);
    select.addEventListener("change", () => {
      feedback(select);
      emit(select);
    });
    select.addEventListener("input", () => feedback(select));
    const drop = select.parentElement.querySelector(".answer-drop");
    drop.addEventListener("dragover", (e) => {
      if (
        selectedToken?.group ===
        drop.closest(".matching-control").dataset.matchGroup
      ) {
        e.preventDefault();
        drop.classList.add("drag-over");
      }
    });
    drop.addEventListener("dragleave", () =>
      drop.classList.remove("drag-over"),
    );
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("drag-over");
      applyToken(drop, selectedToken);
    });
    drop.addEventListener("dragstart", (e) => {
      if (!select.value) {
        e.preventDefault();
        return;
      }
      selectedToken = {
        value: select.value,
        group: drop.closest(".matching-control").dataset.matchGroup,
        source: select.dataset.answer,
      };
      e.dataTransfer.setData("application/json", JSON.stringify(selectedToken));
      e.dataTransfer.effectAllowed = "move";
    });
  });
  document.querySelectorAll(".answer-token,.answer-drop").forEach((el) => {
    let drag = null;
    el.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse") return;
      const control = el.closest(".matching-control");
      const input = control?.querySelector("select");
      const value = input?.value || el.dataset.tokenValue;
      if (!value) return;
      drag = {
        value,
        group: control?.dataset.matchGroup || el.dataset.tokenGroup,
        source: input?.dataset.answer,
      };
      el.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      e.preventDefault();
      document
        .querySelectorAll(".drag-over")
        .forEach((x) => x.classList.remove("drag-over"));
      document
        .elementFromPoint(e.clientX, e.clientY)
        ?.closest(".answer-drop")
        ?.classList.add("drag-over");
    });
    el.addEventListener("pointerup", (e) => {
      if (!drag) return;
      const target = document
        .elementFromPoint(e.clientX, e.clientY)
        ?.closest(".answer-drop");
      if (target) applyToken(target, drag);
      document
        .querySelectorAll(".drag-over")
        .forEach((x) => x.classList.remove("drag-over"));
      drag = null;
    });
    el.addEventListener("pointercancel", () => {
      drag = null;
    });
  });
  document.querySelectorAll("[data-clear-for]").forEach((button) =>
    button.addEventListener("click", () => {
      const s = document.getElementById("answer-" + button.dataset.clearFor);
      s.value = "";
      feedback(s);
      emit(s);
    }),
  );
  document.querySelectorAll(".paired-choices").forEach((group) => {
    const fields = [...group.querySelectorAll("[data-answer]")];
    const checkboxes = [...group.querySelectorAll("[data-pair-choice]")];
    const limit = Number(group.dataset.limit);
    checkboxes.forEach((c) => {
      c.checked = fields.some((f) => f.value === c.value);
    });
    function update(write) {
      const chosen = checkboxes.filter((c) => c.checked).map((c) => c.value);
      checkboxes.forEach((c) => {
        c.disabled = !c.checked && chosen.length >= limit;
      });
      group.querySelector(".pair-progress").textContent =
        `Выбрано ${chosen.length} из ${limit}`;
      fields.forEach((field, i) => {
        if (write) {
          field.value = chosen[i] || "";
          emit(field);
        }
        group.querySelector(
          `[data-pair-output="${field.dataset.answer}"]`,
        ).textContent = field.value || "—";
      });
    }
    checkboxes.forEach((c) => c.addEventListener("change", () => update(true)));
    update(false);
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
  if (state.kind === "reading") {
    const h = document.getElementById("test-highlight");
    document.querySelector(".test-tools").append(h);
    h.textContent = "Highlight";
    h.addEventListener("mousedown", (e) => e.preventDefault());
  }
  if (state.kind === "speaking") {
    cards.forEach((card) => {
      const label = card.querySelector(".question-prompt");
      const lines = label.textContent
        .split(/\n/)
        .map((s) => s.trim())
        .filter(Boolean);
      const prompts =
        card.dataset.part === "2"
          ? [label.textContent]
          : lines.filter((s) => s.endsWith("?"));
      if (!prompts.length) return;
      let i = 0;
      label.textContent = prompts[0];
      const nav = document.createElement("div");
      nav.className = "speaking-card-nav";
      const count = document.createElement("span");
      const prev = document.createElement("button");
      const next = document.createElement("button");
      prev.type = next.type = "button";
      prev.textContent = "Previous";
      next.textContent = "Next question";
      const paint = () => {
        label.textContent = prompts[i];
        count.textContent = i + 1 + " / " + prompts.length;
        prev.disabled = i === 0;
        next.disabled = i === prompts.length - 1;
      };
      prev.onclick = () => {
        i--;
        paint();
      };
      next.onclick = () => {
        i++;
        paint();
      };
      nav.append(prev, count, next);
      label.after(nav);
      paint();
    });
  }
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
