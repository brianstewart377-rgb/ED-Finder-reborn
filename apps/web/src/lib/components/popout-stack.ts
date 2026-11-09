// Tool order is shared by Explore, Inspect and Plan. Registration follows opening order;
// hidden task panels unregister until their tools become visible again.
const panels: {
  element: HTMLElement;
  setLayer?: (layer: number) => void;
}[] = [];

function updateLayers(): void {
  panels.forEach((entry, index) => {
    // A fixed tool inside the map inherits its parent's stacking context.
    // Raise that context with its newest child so visual order still matches
    // Escape order when another workspace tool sits between them.
    const highestChild = panels.findLastIndex((candidate) =>
      entry.element.contains(candidate.element),
    );
    entry.setLayer?.(30 + Math.max(index, highestChild));
  });
}

export function registerPopout(
  element: HTMLElement,
  setLayer?: (layer: number) => void,
): () => void {
  const entry = { element, setLayer };
  panels.push(entry);
  updateLayers();
  return () => {
    const index = panels.indexOf(entry);
    if (index !== -1) panels.splice(index, 1);
    updateLayers();
  };
}

export function isTopmostPopout(element: HTMLElement | undefined): boolean {
  return !!element && panels.at(-1)?.element === element;
}
