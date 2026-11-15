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

  function downloadExport(button) {
    saveEditors();
    var formId = button.getAttribute("data-export");
    var form = formId ? document.getElementById(formId) : button.form;
    if (!form) {
      return;
    }
    button.disabled = true;
    fetch("/api/export", {
      method: "POST",
      headers: {
        "HX-Request": "true",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams(new FormData(form)),
    })
      .then(function (response) {
        var type = response.headers.get("content-type") || "";
        if (type.indexOf("spreadsheet") !== -1) {
          var disposition = response.headers.get("content-disposition") || "";
          var matched = /filename="([^"]+)"/.exec(disposition);
          var filename = matched ? matched[1] : "sclab.xlsx";
          return response.blob().then(function (blob) {
            var url = URL.createObjectURL(blob);
            var link = document.createElement("a");
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(function () {
              URL.revokeObjectURL(url);
            }, 1000);
          });
        }
        return response.text().then(function (html) {
          document.open();
          document.write(html);
          document.close();
        });
      })
      .catch(function () {
        var panel = document.createElement("p");
        panel.className = "message";
        panel.textContent = "Something went wrong. Try again.";
        button.insertAdjacentElement("afterend", panel);
      })
      .then(function () {
        button.disabled = false;
      });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-export]");
    if (!button) {
      return;
    }
    event.preventDefault();
    downloadExport(button);
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
