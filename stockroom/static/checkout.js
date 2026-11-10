const form = document.querySelector("[data-checkout], [data-cart-checkout]");

if (form) {
  const quantity = form.elements.namedItem("quantity");
  const total = form.querySelector("output");
  const submit = form.querySelector("[data-place-order]");
  const submitLabel = submit.querySelector("span");
  const idleLabel = submitLabel.textContent;
  const unitCents = Number(form.dataset.unitCents);
  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

  function updateTotal() {
    if (!total) return;
    total.value = quantity.validity.valid ? money.format((quantity.valueAsNumber * unitCents) / 100) : "—";
  }

  if (total) {
    form.querySelector(".order-total").hidden = false;
    quantity.addEventListener("input", updateTotal);
  }
  form.addEventListener("submit", (event) => {
    if (event.submitter !== submit) return;
    if (submit.disabled) {
      event.preventDefault();
      return;
    }
    submit.disabled = true;
    submitLabel.textContent = "Placing order…";
  });
  window.addEventListener("pageshow", () => {
    submit.disabled = false;
    submitLabel.textContent = idleLabel;
    updateTotal();
  });
  updateTotal();
}
