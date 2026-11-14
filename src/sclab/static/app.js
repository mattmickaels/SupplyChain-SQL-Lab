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
      });
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
})();
