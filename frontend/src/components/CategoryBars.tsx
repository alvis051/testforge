import type { FailureCategory } from "../api/types";

/** Horizontal bars: magnitude is bar length, identity is the text label.
 *  One hue throughout — colour is not carrying identity here, so a
 *  multi-hue categorical palette would add nothing but a legend to read. */
export function CategoryBars({ categories }: { categories: FailureCategory[] }) {
  if (categories.length === 0) {
    return <p className="muted">No failures in the recent runs.</p>;
  }
  const max = Math.max(...categories.map((category) => category.count));

  return (
    <ul className="bars" data-testid="category-bars">
      {categories.map((category) => (
        <li key={category.category} data-testid="category-row">
          <span className="bar-label">{category.category}</span>
          <span className="bar-track">
            <span
              className="bar-fill"
              style={{ width: `${(category.count / max) * 100}%` }}
            />
          </span>
          <span className="bar-value">{category.count}</span>
        </li>
      ))}
    </ul>
  );
}
