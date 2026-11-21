import { useEffect, useRef } from "react";

import type { ProductSummary } from "../api";

interface Props {
  open: boolean;
  products: ProductSummary[];
  onClose: () => void;
  onAsk: (question: string) => void;
}

const BRAND_ORDER = ["Philips Sonicare", "Oral-B"];

/** The full model name without the colour variant ("…, Black Onyx"), so similar models stay distinguishable. */
const fullName = (name: string) => name.split(",")[0].trim();

/** Every product the assistant knows, by brand, with a shortcut to ask about one. */
export function ProductsDialog({ open, products, onClose, onAsk }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {          // a native <dialog>: focus handling, Esc to close and a backdrop come for free
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const brands = [...new Set(products.map((p) => p.brand))].sort(
    (a, b) => (BRAND_ORDER.indexOf(a) + 1 || 99) - (BRAND_ORDER.indexOf(b) + 1 || 99),
  );

  return (
    <dialog
      ref={ref}
      className="products"
      aria-labelledby="products-title"
      onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}      // a click on the backdrop closes it
    >
      <div className="products__inner">
        <header className="products__head">
          <div>
            <h2 id="products-title">Supported products</h2>
            <p>{products.length} electric toothbrushes from public US product pages. Prices are a snapshot.</p>
          </div>
          <button type="button" className="products__close" onClick={onClose} aria-label="Close">×</button>
        </header>

        {brands.map((brand) => {
          const items = products.filter((p) => p.brand === brand)
            .sort((a, b) => (a.price_usd ?? Infinity) - (b.price_usd ?? Infinity));
          return (
            <section key={brand} className="products__brand">
              <h3>{brand} <span className="products__n">{items.length}</span></h3>
              <ul>
                {items.map((p) => (
                  <li key={p.product_id} className="product">
                    <div className="product__info">
                      <span className="product__name">{fullName(p.name)}</span>
                      <span className="product__meta">
                        {p.sku ? `SKU ${p.sku.replace(/^'+|'+$/g, "")}` : p.product_id}
                        {p.series ? ` · ${p.series}` : ""}
                      </span>
                    </div>
                    <span className="product__price">{p.price_usd != null ? `$${p.price_usd.toFixed(2)}` : "price not found"}</span>
                    <div className="product__actions">
                      <button
                        type="button"
                        className="btn btn--small"
                        onClick={() => { onAsk(`Tell me about the ${fullName(p.name)}`); onClose(); }}
                      >
                        Ask
                      </button>
                      <a className="product__link" href={p.source_url} target="_blank" rel="noopener noreferrer"
                         aria-label={`Open the ${fullName(p.name)} product page in a new tab`} title={p.source_url}>
                        ↗
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        {!products.length && <p className="products__empty">The product list could not be loaded. Is the API running?</p>}
      </div>
    </dialog>
  );
}
