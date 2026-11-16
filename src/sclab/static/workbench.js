(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var panel = document.getElementById("sql-workbench");
    var launch = document.getElementById("wb-launch");
    var area = document.getElementById("wb-sql");
    var editor = area.editor;
    var select = document.getElementById("wb-query");
    var status = document.getElementById("wb-status");
    var result = document.getElementById("wb-result");
    var help = document.getElementById("wb-help");
    var form = document.getElementById("workbench-form");
    var storageKey = "sclab.workbench.v1";
    var state = { open: false, mode: "dock", active: "scratch", drafts: {}, rect: {} };
    var busy = false;
    var syncing = false;
    var responses = {};
    try {
      var saved = JSON.parse(sessionStorage.getItem(storageKey));
      if (saved && saved.drafts && typeof saved.drafts === "object") {
        state = Object.assign(state, saved);
      }
    } catch (_) { document.getElementById("wb-storage").hidden = false; }
    // Store plain data only; never restore executable markup from browser storage.
    Object.keys(state.drafts).forEach(function (key) {
      var draft = state.drafts[key];
      if (!draft || typeof draft.sql !== "string" || typeof draft.title !== "string" ||
          (key !== "scratch" && !/^[a-zA-Z0-9_-]+$/.test(key))) delete state.drafts[key];
    });
    if (!state.drafts.scratch) state.drafts.scratch = { title: "Free query", sql: "", prompt: "" };
    if (!state.drafts[state.active]) state.active = "scratch";
    state.mode = state.mode === "float" ? "float" : "dock";
    state.rect = state.rect || {};

    function persist() {
      try { sessionStorage.setItem(storageKey, JSON.stringify(state)); }
      catch (_) { document.getElementById("wb-storage").hidden = false; }
    }
    function getSQL() { return editor ? editor.getValue() : area.value; }
    function setSQL(value) {
      if (editor) editor.setValue(value); else area.value = value;
    }
    function saveDraft() {
      if (syncing) return;
      state.drafts[state.active].sql = getSQL();
      if (!busy && result.textContent.trim()) status.textContent = "Run or Check again to update feedback for this SQL.";
      var inline = document.getElementById("sql-" + state.active);
      if (inline && inline.editor && inline.editor.getValue() !== getSQL()) {
        syncing = true;
        inline.editor.setValue(getSQL());
        syncing = false;
      }
      persist();
    }
    if (editor) editor.on("change", saveDraft); else area.addEventListener("input", saveDraft);

    function clampRect() {
      var r = state.rect;
      var width = Math.min(innerWidth, Math.max(360, Number(r.width) || 620));
      var height = Math.min(innerHeight, Math.max(360, Number(r.height) || 650));
      state.rect = {
        width: width, height: height,
        x: Math.max(0, Math.min(innerWidth - width, Number(r.x) || 0)),
        y: Math.max(0, Math.min(innerHeight - height, Number(r.y) || 0))
      };
    }
    function layout() {
      panel.hidden = !state.open;
      launch.hidden = !!state.open;
      launch.setAttribute("aria-expanded", String(!!state.open));
      launch.textContent = state.drafts[state.active].sql ? "Resume SQL workbench" : "SQL workbench";
      panel.classList.toggle("wb-floating", state.mode === "float");
      document.body.classList.toggle("wb-docked", state.open && state.mode === "dock");
      document.body.classList.toggle("wb-open", !!state.open);
      document.querySelectorAll(".top, main, .app-footer").forEach(function (node) {
        node.inert = !!state.open && innerWidth <= 900;
      });
      document.getElementById("wb-mode").textContent = state.mode === "dock" ? "Float" : "Dock";
      panel.style.cssText = "";
      if (state.mode === "float" && innerWidth > 900) {
        clampRect();
        panel.style.left = state.rect.x + "px";
        panel.style.top = state.rect.y + "px";
        panel.style.width = state.rect.width + "px";
        panel.style.height = state.rect.height + "px";
      }
      if (editor) requestAnimationFrame(function () { editor.refresh(); });
      persist();
    }
    function showDraft() {
      var draft = state.drafts[state.active];
      select.replaceChildren();
      Object.keys(state.drafts).forEach(function (id) {
        var option = new Option(state.drafts[id].title, id);
        select.add(option);
      });
      select.value = state.active;
      syncing = true;
      setSQL(draft.sql);
      syncing = false;
      form.elements.exercise_id.value = state.active === "scratch" ? "" : state.active;
      var task = document.getElementById("wb-task");
      task.hidden = state.active === "scratch";
      document.getElementById("wb-prompt").textContent = draft.prompt || "";
      var lessonLink = document.getElementById("wb-lesson");
      // A stored URL must still be a local lesson link.
      lessonLink.href = /^\/lessons\/[a-zA-Z0-9_-]+#exercise-[a-zA-Z0-9_-]+$/.test(draft.url || "") ? draft.url : "/";
      panel.querySelectorAll("[data-wb-action]").forEach(function (button) {
        button.hidden = button.dataset.wbAction !== "run" && state.active === "scratch";
      });
      result.innerHTML = responses[state.active] || "";
      help.replaceChildren();
      status.textContent = draft.sql ? "Draft restored. Run to refresh results; Check verifies this exercise." : "Write a SELECT query, then Run.";
      if (state.active === "scratch" && draft.sql) status.textContent = "Draft restored. Run to refresh results.";
      layout();
    }
    function open(button) {
      if (busy) {
        state.open = true;
        layout();
        status.textContent = "Query is running. Wait before switching exercises.";
        document.getElementById("wb-minimize").focus();
        return;
      }
      saveDraft();
      var id = button.dataset.workbenchExercise;
      if (id) {
        var section = document.getElementById("exercise-" + id);
        var inline = document.getElementById("sql-" + id);
        state.drafts[id] = {
          title: section.querySelector("h2").textContent,
          sql: inline.editor ? inline.editor.getValue() : inline.value,
          prompt: section.querySelector(".prose").textContent.trim(),
          url: location.pathname + "#exercise-" + id
        };
        state.active = id;
      }
      state.open = true;
      showDraft();
      if (editor) editor.focus(); else area.focus();
    }
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-workbench-exercise], #wb-launch");
      if (button) open(button);
    });
    select.addEventListener("change", function () {
      saveDraft(); state.active = select.value; showDraft();
    });
    document.getElementById("wb-minimize").addEventListener("click", function () {
      saveDraft(); state.open = false; layout();
      launch.focus();
    });
    panel.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        document.getElementById("wb-minimize").click();
      }
    });
    document.getElementById("wb-mode").addEventListener("click", function () {
      state.mode = state.mode === "dock" ? "float" : "dock";
      layout();
    });
    window.addEventListener("resize", layout);
    window.addEventListener("pagehide", saveDraft);

    // Synchronize the inline editor with its own draft, not with whichever query is active.
    document.querySelectorAll("[data-workbench-exercise]").forEach(function (button) {
      var id = button.dataset.workbenchExercise;
      var inline = document.getElementById("sql-" + id);
      if (!inline.editor) return;
      if (state.drafts[id]) inline.editor.setValue(state.drafts[id].sql);
      inline.editor.on("change", function () {
        if (syncing || !state.drafts[id]) return;
        state.drafts[id].sql = inline.editor.getValue();
        if (state.active === id) {
          syncing = true; setSQL(inline.editor.getValue()); syncing = false;
        }
        persist();
      });
    });

    async function run(action, hintLevel) {
      if (busy) return;
      saveDraft();
      var id = state.active;
      if (id === "scratch" && action !== "run") return;
      var url = action === "run" ? "/api/run" : "/api/" + action + "/" + encodeURIComponent(id) + (action === "hint" ? "/" + (hintLevel || 1) : "");
      var isQuery = action === "run" || action === "check";
      var destination = isQuery ? result : help;
      var sql = getSQL();
      busy = true;
      select.disabled = true;
      panel.querySelectorAll("[data-wb-action]").forEach(function (button) { button.disabled = true; });
      status.textContent = isQuery ? "Running query…" : "Loading help…";
      destination.setAttribute("aria-busy", "true");
      try {
        var options = { headers: { "HX-Request": "true" } };
        if (isQuery) {
          if (editor) editor.save();
          options.method = "POST";
          options.body = new URLSearchParams(new FormData(form));
        }
        var response = await fetch(url, options);
        if (!response.ok) throw new Error("Request failed");
        destination.innerHTML = await response.text();
        // Exports must use this query even when its original lesson is no longer open.
        destination.querySelectorAll("[data-export]").forEach(function (button) { button.dataset.export = "workbench-form"; });
        destination.querySelectorAll("[hx-get]").forEach(function (button) {
          var next = Number(button.getAttribute("hx-get").split("/").pop());
          button.removeAttribute("hx-get");
          button.addEventListener("click", function () { run("hint", next); });
        });
        if (isQuery) responses[id] = destination.innerHTML;
        var inlineCheck = document.getElementById("check-" + id);
        if (action === "check" && inlineCheck) {
          inlineCheck.innerHTML = destination.innerHTML;
          inlineCheck.querySelectorAll("[data-export]").forEach(function (button) { button.dataset.export = "form-" + id; });
        }
        status.textContent = getSQL() === sql ? (isQuery ? "Response ready below." : "Help ready below.") : "Response is for the previous SQL. Run again after your edits.";
      } catch (_) {
        destination.textContent = "Could not reach the lab. Your SQL is still here. Try again.";
        status.textContent = "Request failed. Retry when the lab is available.";
      } finally {
        busy = false;
        select.disabled = false;
        panel.querySelectorAll("[data-wb-action]").forEach(function (button) { button.disabled = false; });
        destination.removeAttribute("aria-busy");
      }
    }
    panel.querySelectorAll("[data-wb-action]").forEach(function (button) {
      button.addEventListener("click", function () { run(button.dataset.wbAction); });
    });
    form.addEventListener("submit", function (event) { event.preventDefault(); run("run"); });

    function geometryControl(id, resizing) {
      var handle = document.getElementById(id);
      handle.addEventListener("pointerdown", function (event) {
        if (event.button !== 0 || state.mode !== "float") return;
        event.preventDefault();
        handle.focus();
        handle.setPointerCapture(event.pointerId);
        var startX = event.clientX, startY = event.clientY;
        var original = Object.assign({}, state.rect);
        function move(e) {
          state.rect = Object.assign({}, original);
          state.rect[resizing ? "width" : "x"] += e.clientX - startX;
          state.rect[resizing ? "height" : "y"] += e.clientY - startY;
          layout();
        }
        function stop() {
          handle.removeEventListener("pointermove", move);
          handle.removeEventListener("lostpointercapture", stop);
        }
        handle.addEventListener("pointermove", move);
        handle.addEventListener("lostpointercapture", stop);
      });
      handle.addEventListener("keydown", function (event) {
        if (state.mode !== "float" || !/^Arrow(Left|Right|Up|Down)$/.test(event.key)) return;
        event.preventDefault();
        var horizontal = event.key === "ArrowLeft" || event.key === "ArrowRight";
        var delta = (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) * (event.shiftKey ? 40 : 10);
        state.rect[resizing ? (horizontal ? "width" : "height") : (horizontal ? "x" : "y")] += delta;
        layout();
      });
    }
    geometryControl("wb-move", false);
    geometryControl("wb-resize", true);
    showDraft();
    if (state.open && innerWidth <= 900) document.getElementById("wb-minimize").focus();
  });
})();
