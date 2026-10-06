/* Copy the feed address. External file: the CSP forbids inline scripts. */
(function () {
  var button = document.querySelector("[data-copy]");
  if (!button) return;
  var field = document.getElementById(button.getAttribute("data-copy"));
  var status = document.getElementById("feed-copied");
  button.addEventListener("click", function () {
    field.select();
    var done = function () { status.textContent = "Copied. Paste it into your reader."; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(field.value).then(done, function () { document.execCommand("copy"); done(); });
    } else {
      document.execCommand("copy");
      done();
    }
  });
})();
