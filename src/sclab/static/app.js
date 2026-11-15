(function () {
  function saveEditors() {
    document.querySelectorAll("textarea[data-editor]").forEach(function (el) {
      if (el.editor) {
        el.editor.save();
      }
    });
  }

  function runEditor(textarea) {
    saveEditors();
    var form = textarea.form;
    if (!form) {
      return;
    }
    var button = form.querySelector("[data-run]");
    if (button) {
      button.click();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (typeof CodeMirror === "undefined") {
      return;
    }
    document.querySelectorAll("textarea[data-editor]").forEach(function (el) {
      var editor = CodeMirror.fromTextArea(el, {
        mode: "text/x-sql",
        lineNumbers: true,
        matchBrackets: true,
        viewportMargin: Infinity,
      });
      var input = editor.getInputField();
      var label = el.getAttribute("aria-label") || "SQL";
      input.setAttribute("aria-label", label);
      if (el.id) {
        input.id = el.id + "-input";
      }
      input.setAttribute("name", (el.id || "sql") + "-editor");
      el.editor = editor;
      editor.on("change", function () {
        editor.save();
      });
      editor.setOption("extraKeys", {
        "Ctrl-Enter": function () {
          runEditor(el);
        },
        "Cmd-Enter": function () {
          runEditor(el);
        },
      });
    });
  });

  document.body.addEventListener("htmx:configRequest", saveEditors);
  document.body.addEventListener("htmx:responseError", function (event) {
    var target = event.detail && event.detail.target;
    if (!target) {
      return;
    }
    var panel = document.createElement("p");
    panel.className = "message";
    panel.textContent = "Something went wrong. Try again.";
    target.replaceChildren(panel);
  });
})();
