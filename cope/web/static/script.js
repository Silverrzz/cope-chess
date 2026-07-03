document.querySelectorAll("tr[data-href]").forEach((row) => {
  row.addEventListener("click", () => {
    window.location.href = row.dataset.href;
  });
});
