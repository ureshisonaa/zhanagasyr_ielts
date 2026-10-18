"use strict";
(() => {
  const state = JSON.parse(document.getElementById("exam-state").textContent);
  const form = document.getElementById("answers-form");
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  const key = `zg-draft-${state.attempt}-${state.section}`;
  const alert = document.getElementById("exam-alert");
  const status = document.getElementById("save-status");
  let dirty = false,
    conflicted = false,
    closed = false,
    saving = null,
    sequence = 0,
    closing = false;
  let syncedServer = Date.parse(state.server_now),
    syncedClock = performance.now();
  let deadline = Date.parse(state.deadline);
  let activeRecorders = 0;
  const pendingAudio = new Set();
  const showError = (text) => {
    alert.hidden = false;
    alert.textContent = text;
  };
  const setStatus = (text, type = "") => {
    status.textContent = text;
    status.className = `save-pill ${type}`;
  };
  const serverNow = () => syncedServer + performance.now() - syncedClock;
  const syncClock = (data) => {
    syncedServer = Date.parse(data.server_now);
    syncedClock = performance.now();
    if (data.deadline) deadline = Date.parse(data.deadline);
  };
  function readAnswers() {
    const answers = {};
    form.querySelectorAll("[data-answer]").forEach((input) => {
      const name = input.dataset.answer;
      if (input.type === "checkbox") {
        if (!answers[name]) answers[name] = [];
        if (input.checked) answers[name].push(input.value);
      } else if (input.type === "radio") {
        if (!(name in answers)) answers[name] = "";
        if (input.checked) answers[name] = input.value;
      } else answers[name] = input.value;
    });
    return answers;
  }
  function fillAnswers(answers) {
    form.querySelectorAll("[data-answer]").forEach((input) => {
      const value = answers[input.dataset.answer] ?? "";
      if (input.type === "checkbox")
        input.checked = Array.isArray(value) && value.includes(input.value);
      else if (input.type === "radio") input.checked = input.value === value;
      else input.value = value;
    });
  }
  function persist() {
    try {
      localStorage.setItem(
        key,
        JSON.stringify({ revision: state.revision, responses: readAnswers() }),
      );
    } catch {
      showError(
        "Локальное сохранение недоступно. Дождитесь подтверждения сервера перед закрытием страницы.",
      );
    }
  }
  function conflict() {
    conflicted = true;
    setStatus("Ответы требуют сверки", "failed");
    showError(
      "Версия ответов на сервере изменилась. Возможно, экзамен открыт в другой вкладке. Скачайте локальные ответы, затем обновите страницу.",
    );
    document.getElementById("conflict-controls").hidden = false;
  }
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (
      saved &&
      JSON.stringify(saved.responses) !== JSON.stringify(state.responses)
    ) {
      fillAnswers(saved.responses);
      dirty = true;
      if (saved.revision !== state.revision) conflict();
      else setStatus("Восстановлен локальный черновик", "pending");
    }
  } catch {
    showError(
      "Не удалось восстановить локальный черновик. Показаны сохранённые сервером ответы.",
    );
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers: { "X-CSRFToken": csrf, ...options.headers },
        signal: controller.signal,
      });
      if (response.redirected)
        throw new Error(
          "Сессия завершилась. Сохраните локальную копию ответов и войдите снова.",
        );
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || "Сервер отклонил запрос.");
        error.conflict = data.conflict;
        throw error;
      }
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }
  function markClosed() {
    closed = true;
    setStatus("Секция завершена", "pending");
    document.getElementById("submit-section").disabled = true;
    if (dirty || pendingAudio.size || activeRecorders) {
      showError(
        "Время секции истекло. Сервер зафиксировал ранее сохранённые ответы. Несинхронизированные изменения больше не принимаются; сохраните локальную копию для обращения к администратору.",
      );
      document.getElementById("conflict-controls").hidden = false;
      document.getElementById("reload-attempt").textContent = "Перейти дальше";
    } else {
      try {
        localStorage.removeItem(key);
      } catch {
        /* Ignore unavailable storage. */
      }
      location.reload();
    }
  }
  async function save(submit = false) {
    if (saving) {
      await saving;
      if (submit) return save(true);
      return;
    }
    if (conflicted || closed || (!dirty && !submit)) return false;
    const snapshot = readAnswers();
    const sentSequence = sequence;
    setStatus(submit ? "Отправляем секцию…" : "Сохраняем…", "pending");
    saving = (async () => {
      try {
        const data = await request(state.save_url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            index: state.section,
            revision: state.revision,
            responses: snapshot,
            submit,
          }),
        });
        if (data.saved) {
          state.revision = data.revision;
          dirty = sequence !== sentSequence;
          if (dirty) persist();
          else {
            try {
              localStorage.removeItem(key);
            } catch {
              /* optional local storage */
            }
          }
          setStatus(
            dirty ? "Есть новые изменения…" : "● Сохранено на сервере",
            dirty ? "pending" : "",
          );
        }
        if (data.closed) {
          markClosed();
          return true;
        }
        return true;
      } catch (error) {
        if (error.conflict) conflict();
        else {
          setStatus("Не подтверждено сервером", "failed");
          showError(
            error.message === "Failed to fetch" || error.name === "AbortError"
              ? "Нет подтверждения сервера. Ответы сохранены на этом устройстве; повторим отправку после восстановления соединения."
              : error.message,
          );
        }
        persist();
        return false;
      }
    })();
    try {
      return await saving;
    } finally {
      saving = null;
    }
  }
  function progress() {
    const answers = readAnswers();
    let count = 0;
    for (const [id, value] of Object.entries(answers)) {
      const has = Array.isArray(value)
        ? value.length > 0
        : value.trim().length > 0;
      const card = document.getElementById(`question-${id}`);
      const link = document.querySelector(
        `.question-nav a[href="#question-${id}"]`,
      );
      link?.classList.toggle("answered", has);
      if (has) count++;
      const wordCount = card?.querySelector(".word-counter");
      if (wordCount)
        wordCount.textContent = `${String(value).trim().split(/\s+/u).filter(Boolean).length} слов`;
    }
    const total = Object.keys(answers).length;
    document.getElementById("answer-progress").textContent = total
      ? `${count} / ${total} ответов`
      : "Speaking · 3 части";
  }
  let debounce;
  form.addEventListener("input", (event) => {
    if (!event.target.matches("[data-answer]")) return;
    dirty = true;
    sequence++;
    persist();
    progress();
    if (!conflicted && !closed) setStatus("Сохраняем изменения…", "pending");
    clearTimeout(debounce);
    debounce = setTimeout(() => save(), 700);
  });
  progress();
  setInterval(() => {
    if (dirty && navigator.onLine) save();
  }, 5000);
  window.addEventListener("online", () => {
    if (!conflicted) {
      alert.hidden = true;
      save();
    }
  });
  window.addEventListener("offline", () =>
    setStatus("Без сети · локальный черновик", "pending"),
  );
  window.addEventListener("beforeunload", (event) => {
    if (dirty || pendingAudio.size || activeRecorders) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  async function poll() {
    if (closed || closing) return;
    closing = true;
    try {
      const data = await request(state.state_url);
      syncClock(data);
      if (data.status !== "active" || data.index !== state.section)
        markClosed();
    } catch {
      if (serverNow() >= deadline)
        showError(
          "Время истекло. Ожидаем соединения с сервером, чтобы перейти дальше.",
        );
    } finally {
      closing = false;
    }
  }
  setInterval(poll, 15000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
  function renderClock() {
    const seconds = Math.max(0, Math.ceil((deadline - serverNow()) / 1000));
    document.getElementById("countdown").textContent =
      `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    document.querySelector(".timer").classList.toggle("urgent", seconds < 120);
    if (seconds === 0 && !closed && !closing) poll();
  }
  renderClock();
  setInterval(renderClock, 1000);
  const dialog = document.getElementById("submit-dialog");
  document.getElementById("submit-section").addEventListener("click", () => {
    if (pendingAudio.size || activeRecorders) {
      showError(
        "Остановите запись и дождитесь подтверждения загрузки всех аудиофайлов.",
      );
      return;
    }
    if (conflicted) {
      showError(
        "Сначала разрешите конфликт ответов: сохраните локальную копию и обновите страницу.",
      );
      return;
    }
    document.getElementById("submit-summary").textContent =
      document.getElementById("answer-progress").textContent;
    dialog.showModal();
  });
  document
    .getElementById("cancel-submit")
    .addEventListener("click", () => dialog.close());
  document
    .getElementById("confirm-submit")
    .addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      await save(true);
      dialog.close();
      button.disabled = false;
    });
  document.getElementById("download-draft").addEventListener("click", () => {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            attempt: state.attempt,
            section: state.kind,
            responses: readAnswers(),
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "my-local-answers.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.getElementById("reload-attempt").addEventListener("click", () => {
    if (
      !confirm(
        "Сохраните нужные локальные ответы перед обновлением. Продолжить?",
      )
    )
      return;
    dirty = false;
    try {
      localStorage.removeItem(key);
    } catch {}
    location.reload();
  });
  const play = document.getElementById("play-listening");
  play?.addEventListener("click", async () => {
    play.disabled = true;
    const audio = document.getElementById("listening-audio");
    const audioStatus = document.getElementById("audio-status");
    try {
      const data = await request(play.dataset.url, { method: "POST" });
      audio.src = data.url;
      await audio.play();
      audioStatus.textContent = "● Воспроизведение…";
      audio.addEventListener(
        "ended",
        () => {
          audioStatus.textContent = "Прослушивание завершено";
        },
        { once: true },
      );
      audio.addEventListener("error", () => {
        audioStatus.textContent =
          "Ошибка воспроизведения. Сообщите администратору; таймер продолжает идти.";
      });
    } catch (error) {
      audioStatus.textContent = error.message;
    }
  });
  // Audio blobs survive refresh in IndexedDB until acknowledged by the server.
  const dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open("zg-recordings", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("blobs");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  dbPromise.catch(() => {});
  async function audioStore(action, id, value) {
    const db = await dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction(
        "blobs",
        action === "get" ? "readonly" : "readwrite",
      );
      const store = tx.objectStore("blobs");
      const req =
        action === "put"
          ? store.put(value, id)
          : action === "get"
            ? store.get(id)
            : store.delete(id);
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = () => reject(tx.error);
    });
  }
  document.querySelectorAll("[data-recording]").forEach((box) => {
    const id = `${state.attempt}-${box.dataset.recording}`;
    const start = box.querySelector(".record-start"),
      stop = box.querySelector(".record-stop");
    const label = box.querySelector(".record-status"),
      preview = box.querySelector(".record-preview");
    const retry = box.querySelector(".record-retry"),
      download = box.querySelector(".record-download");
    let recorder,
      stream,
      chunks = [],
      pendingFile,
      uploading = false,
      objectUrl,
      limitTimeout;
    function previewBlob(file) {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(file);
      preview.src = objectUrl;
      preview.hidden = false;
      download.href = objectUrl;
      download.download = file.name || "speaking.webm";
      download.hidden = false;
    }
    async function upload() {
      if (!pendingFile || uploading) return;
      pendingAudio.add(id);
      uploading = true;
      retry.hidden = true;
      label.textContent = "Загружаем запись…";
      const data = new FormData();
      data.append("file", pendingFile, pendingFile.name || "speaking.webm");
      try {
        await request(box.dataset.upload, { method: "POST", body: data });
        pendingAudio.delete(id);
        pendingFile = null;
        try {
          await audioStore("delete", id);
        } catch {
          /* Server acknowledged; local cleanup is best effort. */
        }
        label.textContent = "✓ Запись сохранена на сервере";
        document
          .querySelector(
            `.question-nav a[href="#question-${box.dataset.recording}"]`,
          )
          ?.classList.add("answered");
      } catch (error) {
        label.textContent = `Запись не загружена: ${error.message}. Скачайте копию или повторите отправку.`;
        retry.hidden = false;
      } finally {
        uploading = false;
      }
    }
    async function keep(file) {
      if (uploading) {
        label.textContent = "Дождитесь завершения текущей загрузки.";
        return;
      }
      if (file.size > 25 * 1024 * 1024) {
        label.textContent =
          "Файл больше 25 МБ. Выберите запись меньшего размера.";
        return;
      }
      pendingFile = file;
      pendingAudio.add(id);
      previewBlob(file);
      try {
        await audioStore("put", id, file);
      } catch {
        label.textContent =
          "Локальное хранилище недоступно. Скачайте копию записи.";
      }
      await upload();
    }
    audioStore("get", id)
      .then((file) => {
        if (file) {
          pendingFile = file;
          pendingAudio.add(id);
          previewBlob(file);
          label.textContent =
            "Восстановлена неотправленная запись. Повторите загрузку.";
          retry.hidden = false;
        }
      })
      .catch(() => {});
    start.addEventListener("click", async () => {
      if (activeRecorders || uploading || closed) {
        label.textContent = "Сначала завершите текущую запись или загрузку.";
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mime = [
          "audio/webm;codecs=opus",
          "audio/ogg;codecs=opus",
          "audio/mp4",
        ].find((type) => MediaRecorder.isTypeSupported(type));
        if (!mime)
          throw new Error(
            "Браузер не поддерживает доступные форматы. Загрузите аудиофайл.",
          );
        recorder = new MediaRecorder(stream, {
          mimeType: mime,
          audioBitsPerSecond: 64000,
        });
        chunks = [];
        recorder.ondataavailable = (event) => {
          if (event.data.size) chunks.push(event.data);
        };
        recorder.onstop = async () => {
          clearTimeout(limitTimeout);
          stream.getTracks().forEach((track) => track.stop());
          activeRecorders--;
          box.classList.remove("recording");
          start.disabled = false;
          stop.disabled = true;
          const extension = mime.includes("mp4")
            ? "m4a"
            : mime.includes("ogg")
              ? "ogg"
              : "webm";
          await keep(
            new File(chunks, `speaking-${box.dataset.recording}.${extension}`, {
              type: mime,
            }),
          );
        };
        recorder.start(1000);
        activeRecorders++;
        start.disabled = true;
        stop.disabled = false;
        box.classList.add("recording");
        label.textContent =
          "● Идёт запись. Нажмите «Остановить», когда закончите.";
        limitTimeout = setTimeout(
          () => {
            if (recorder.state === "recording") recorder.stop();
          },
          14 * 60 * 1000,
        );
      } catch (error) {
        stream?.getTracks().forEach((track) => track.stop());
        label.textContent = `Микрофон недоступен: ${error.message}. Проверьте разрешения или загрузите файл.`;
      }
    });
    stop.addEventListener("click", () => {
      if (recorder?.state === "recording") recorder.stop();
    });
    retry.addEventListener("click", upload);
    box.querySelector(".record-file").addEventListener("change", (event) => {
      if (event.target.files[0]) keep(event.target.files[0]);
    });
    window.addEventListener("online", () => {
      if (pendingFile && !closed) upload();
    });
  });
  document.getElementById("prep-start")?.addEventListener("click", (event) => {
    event.currentTarget.disabled = true;
    const prepKey = `zg-prep-${state.attempt}`;
    let end;
    try {
      end = Number(localStorage.getItem(prepKey)) || serverNow() + 60000;
      localStorage.setItem(prepKey, String(end));
    } catch {
      end = serverNow() + 60000;
    }
    const tick = setInterval(() => {
      const left = Math.max(0, Math.ceil((end - serverNow()) / 1000));
      document.getElementById("prep-countdown").textContent = left
        ? `${left} сек.`
        : "Можно записывать ответ";
      if (!left) clearInterval(tick);
    }, 250);
  });
  if (dirty && !conflicted) save();
})();
