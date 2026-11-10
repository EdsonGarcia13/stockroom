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

const cart = document.querySelector(".cart-layout");

if (cart) {
  const quantityInputs = [...cart.querySelectorAll(".cart-item-form input[name=quantity]")];
  const savingMessage = cart.querySelector("[data-cart-saving]");
  let saving = false;

  for (const input of quantityInputs) {
    const update = input.form.querySelector(".cart-update");
    const remove = input.form.querySelector('button[value="remove"]');
    remove.addEventListener("pointerdown", (event) => {
      if (event.button === 0 && document.activeElement === input && input.value !== input.defaultValue) {
        event.preventDefault();
      }
    });
    input.addEventListener("change", () => {
      if (input.value !== input.defaultValue && input.validity.valid) {
        input.form.requestSubmit(update);
      }
    });
    update.hidden = true;
  }

  cart.addEventListener("submit", (event) => {
    if (saving) {
      event.preventDefault();
      event.stopImmediatePropagation();
    } else if (event.submitter?.matches(".cart-update")) {
      saving = true;
      savingMessage.hidden = false;
    } else if (event.target.matches("[data-cart-checkout]")) {
      const edited = quantityInputs.find(input => input.value !== input.defaultValue);
      if (edited) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (edited.reportValidity()) {
          edited.form.requestSubmit(edited.form.querySelector(".cart-update"));
        }
      }
    }
  }, { capture: true });

  window.addEventListener("pageshow", () => {
    saving = false;
    savingMessage.hidden = true;
  });
}
