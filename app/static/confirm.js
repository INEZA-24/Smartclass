"use strict";

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm-message]");
  if (form && !window.confirm(form.dataset.confirmMessage)) {
    event.preventDefault();
  }
});
