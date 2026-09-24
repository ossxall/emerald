// Emerald
// Copyright (C) 2026 Juan José Caballero Rey - https://github.com/rey-sudo
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation version 3 of the License.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.

import { Mark, mergeAttributes } from "@tiptap/core";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";

const MultiSelectStateKey = new PluginKey("multiSelectState");

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface MultiSelectOptions {
  /**
   * Additional HTML attributes merged into every <multiSelect> element.
   * @default {}
   */
  HTMLAttributes: Record<string, any>;

  /**
   * Color palette cycled through on each new selection.
   * Supports any valid CSS color string.
   */
  colors: string[];
}

export interface SelectionEntry {
  /** Unique identifier assigned to this selection. */
  id: string;
  /**
   * Monotonic index reflecting the order in which this selection was created.
   * Selections removed and re-added always receive a higher number than all
   * previous ones; the counter never resets during a session.
   */
  order: number;
  /**
   * Full text content of the selection, as it appears in the document.
   * Adjacent text nodes belonging to the same mark are concatenated.
   */
  text: string;
  /** CSS color assigned to this selection. */
  color: string;
}

export interface MultiSelectStorage {
  /**
   * Returns all selections currently in the document, sorted by the order
   * in which they were created (not by their position in the document).
   *
   * @example
   * const entries = editor.storage.multiSelect.getSelectionsInOrder(editor.state.doc)
   * entries.forEach(e => console.log(`#${e.order}: "${e.text}"`))
   */
  getSelectionsInOrder(doc: ProseMirrorNode): SelectionEntry[];

  /**
   * Returns the number of distinct selections currently in the document.
   *
   * @example
   * const count = editor.storage.multiSelect.getSelectionCount(editor.state.doc)
   */
  getSelectionCount(doc: ProseMirrorNode): number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Command augmentation
// ─────────────────────────────────────────────────────────────────────────────

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    multiSelect: {
      /**
       * Toggle a selection mark on the current editor selection range.
       *
       * - If the range has **no** multiSelect mark → a new numbered, colored
       *   mark is added and the selection counter is incremented.
       * - If the range **already has** a multiSelect mark → the mark is removed.
       *
       * Returns `false` (no-op) when the current selection is collapsed (empty).
       *
       * @example
       * editor.commands.toggleSelection()
       */
      toggleSelection: () => ReturnType;

      /**
       * Remove **every** multiSelect mark from the entire document in a single
       * transaction and reset the color-cycling index.
       *
       * Note: the monotonic order counter is intentionally NOT reset so that
       * newly added selections after a clear always carry higher numbers, making
       * them easy to distinguish in external logs or undo history.
       *
       * @example
       * editor.commands.clearAllSelections()
       */
      clearAllSelections: () => ReturnType;
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

const MARK_NAME = "multiSelect";

/**
 * Walk the document and aggregate all multiSelect marks into SelectionEntry
 * objects, concatenating adjacent text nodes that share the same mark id.
 * The returned array is sorted by `order` (creation order).
 */
function collectSelections(doc: ProseMirrorNode): SelectionEntry[] {
  const map = new Map<string, SelectionEntry>();

  doc.descendants((node) => {
    if (!node.isText) return;

    for (const mark of node.marks) {
      if (mark.type.name !== MARK_NAME) continue;

      const { id, order, color } = mark.attrs as {
        id: string;
        order: number;
        color: string;
      };

      const existing = map.get(id);
      if (existing) {
        existing.text += node.text ?? "";
      } else {
        map.set(id, { id, order, color, text: node.text ?? "" });
      }
    }
  });

  return [...map.values()].sort((a, b) => a.order - b.order);
}

function countDistinctSelections(doc: ProseMirrorNode): number {
  const ids = new Set<string>();
  doc.descendants((node) => {
    for (const mark of node.marks) {
      if (mark.type.name === MARK_NAME) ids.add(mark.attrs.id as string);
    }
  });
  return ids.size;
}

function getMaxOrder(doc: ProseMirrorNode): number {
  let max = 0;
  doc.descendants((node) => {
    node.marks.forEach((mark) => {
      if (mark.type.name === MARK_NAME) {
        const order = mark.attrs.order || 0;
        if (order > max) max = order;
      }
    });
  });
  return max;
}

// ─────────────────────────────────────────────────────────────────────────────
// Extension
// ─────────────────────────────────────────────────────────────────────────────

export const MultiSelect = Mark.create<MultiSelectOptions, MultiSelectStorage>({
  name: MARK_NAME,

  addOptions() {
    return {
      HTMLAttributes: {},
      colors: [
        "#FFD166", // amber
        "#06D6A0", // mint
        "#118AB2", // ocean
        "#EF476F", // rose
        "#A8DADC", // powder blue
        "#C77DFF", // lavender
        "#F4845F", // coral
        "#B7E4C7", // sage

        "#2A9D8F", // persian green
        "#83C5BE", // pale teal
        "#52796F", // slate green
        "#A3B18A", // moss grey
        "#95D5B2", // light green

        "#457B9D", // steel blue
        "#48CAE4", // sky cyan
        "#669BBC", // dusty blue
        "#8D99AE", // cool grey
        "#C8B6FF", // periwinkle

        "#9D4EDD", // soft amethyst
        "#CDB4DB", // pale violet
        "#E5989B", // pastel mauve
        "#B5838D", // dusty rose
        "#F2B5D4", // cotton candy

        "#E07A5F", // terracotta
        "#E9C46A", // saffron
        "#F4A261", // sandy brown
        "#D4A373", // camel
        "#CB997E", // warm taupe
        "#A98467", // mocha
        "#C1666B", // muted brick
      ],
    };
  },

  // ── Storage: mutable state + public API methods ──────────────────────────

  addStorage(): MultiSelectStorage {
    return {
      getSelectionsInOrder(doc: ProseMirrorNode): SelectionEntry[] {
        return collectSelections(doc);
      },
      getSelectionCount(doc: ProseMirrorNode): number {
        return countDistinctSelections(doc);
      },
    };
  },

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: MultiSelectStateKey,
        state: {
          init: () => ({ counter: 0, colorIndex: 0 }),
          apply: (tr, pluginState) => {
            const meta = tr.getMeta(MultiSelectStateKey);
            if (meta) {
              return {
                counter: meta.counter ?? pluginState.counter,
                colorIndex: meta.colorIndex ?? pluginState.colorIndex,
              };
            }
            return pluginState;
          },
        },
      }),
    ];
  },

  // ── Attributes ────────────────────────────────────────────────────────────

  addAttributes() {
    return {
      /** Unique selection identifier (used to aggregate text across text nodes). */
      id: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-ms-id"),
        renderHTML: (attrs) => ({ "data-ms-id": attrs.id }),
      },

      /** Creation order – drives the numbered badge and sorted extraction. */
      order: {
        default: 0,
        parseHTML: (el) => Number(el.getAttribute("data-ms-order") ?? 0),
        renderHTML: (attrs) => ({ "data-ms-order": attrs.order }),
      },

      /** Background color for this selection. */
      color: {
        default: "#FFD166",
        parseHTML: (el) => el.getAttribute("data-ms-color"),
        renderHTML: (attrs) => ({
          "data-ms-color": attrs.color,
          style: `background-color: ${attrs.color}20; border-bottom: 2px solid ${attrs.color};`,
        }),
      },
    };
  },

  // ── HTML serialization ────────────────────────────────────────────────────

  parseHTML() {
    // Se acepta `multiSelect` (nombre canónico del mark) y `mark` (forma
    // anterior) para no perder selecciones guardadas con el tag viejo.
    return [
      { tag: "mark[data-ms-id]" },
      { tag: "multiSelect[data-ms-id]" },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return [
      "multiSelect",
      mergeAttributes(
        { class: "ms-mark" },
        this.options.HTMLAttributes,
        HTMLAttributes,
      ),
      0, // content hole
    ];
  },

  // ── Lifecycle: inject styles once on mount ────────────────────────────────

  onCreate() {
    injectStyles();
  },

  // ── Commands ──────────────────────────────────────────────────────────────

  addCommands() {
    return {
      // ── toggleSelection ────────────────────────────────────────────────────
      toggleSelection:
        () =>
        ({ state, tr, dispatch }) => {
          const { from, to, empty } = state.selection;

          // 1. Si no hay selección, no hacemos nada
          if (empty) return false;

          const markType = state.schema.marks[this.name];
          if (!markType) return false;

          const hasMark = state.doc.rangeHasMark(from, to, markType);

          if (hasMark) {
            // Opcional: Si quieres que al hacer click de nuevo se borre:
            if (dispatch) tr.removeMark(from, to, markType);
            return true;
           // return false;
          }

          // 2. LA CLAVE: Calculamos el número basado SOLO en lo que existe en el doc
          // Al hacer UNDO, el mark desaparece del doc, por lo que getMaxOrder bajará automáticamente.
          const currentMax = getMaxOrder(state.doc);
          const order = currentMax + 1;

          // 3. El color se vincula al número para que sea consistente
          const color =
            this.options.colors[(order - 1) % this.options.colors.length];

          // 4. ID único para este mark
          const id = `ms-${Date.now().toString(36)}-${Math.random()
            .toString(36)
            .slice(2, 7)}`;

          if (dispatch) {
            tr.addMark(from, to, markType.create({ id, order, color }));
          }

          return true;
        },

      // ── clearAllSelections ─────────────────────────────────────────────────
      clearAllSelections:
        () =>
        ({ tr, dispatch, state }) => {
          const markType = state.schema.marks[this.name];

          // Collect all ranges to remove in a single pass
          state.doc.descendants((node, pos) => {
            if (!node.isText) return;
            for (const mark of node.marks) {
              if (mark.type === markType) {
                tr.removeMark(pos, pos + node.nodeSize, markType);
              }
            }
          });

          // Leemos el estado actual
          const pluginState = MultiSelectStateKey.getState(state);

          if (dispatch) {
            // Reseteamos el colorIndex a 0, pero mantenemos el counter intacto
            tr.setMeta(MultiSelectStateKey, {
              counter: pluginState.counter,
              colorIndex: 0,
            });
          }

          return true;
        },
    };
  },

  // ── Keyboard shortcut ─────────────────────────────────────────────────────

  addKeyboardShortcuts() {
    return {
      // Cmd/Ctrl + Shift + X → toggle selection
      "Mod-Shift-x": () => this.editor.commands.toggleSelection(),
    };
  },
});

// ─────────────────────────────────────────────────────────────────────────────
// Style injection
// ─────────────────────────────────────────────────────────────────────────────

const STYLE_ID = "tiptap-multi-select-styles";

function injectStyles(): void {
  if (typeof document === "undefined") return; // SSR guard
  if (document.getElementById(STYLE_ID)) return; // already injected

  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = /* css */ `

    /* ── Base mark ─────────────────────────────────────────────── */
    .ms-mark {
      border-radius: 2px;
      padding: 1px 2px;
      position: relative;
      cursor: pointer;
      transition:
        filter        0.15s ease,
        box-shadow    0.15s ease,
        outline-color 0.15s ease;
      /* Subtle lift so the mark reads above plain text */
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.08);
      color: var(--ui-text);
    }

    /* ── Hover ──────────────────────────────────────────────────── */
    .ms-mark:hover {
      filter: brightness(0.85) saturate(1.3);
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.18);
      outline: 1.5px solid currentColor;
      outline-offset: 1px;
      z-index: 1;
    }

    /* ── Numbered badge (top-right corner) ──────────────────────── */
    /*
     * Uses the data-ms-order attribute set by renderHTML so no JS
     * update is needed – the browser always reads the live value.
     */
    .ms-mark::after {
      content: attr(data-ms-order);
      position: absolute;
      top: 0px;
      right: -6px;
      min-width: 14px;
      height: 14px;
      padding: 0 3px;
      font-size: calc(var(--text-base) / 2);
      font-family: ui-monospace, 'Cascadia Code', 'Fira Code', monospace;
      line-height: 14px;
      text-align: center;
      letter-spacing: -0.3px;
      background: var(--ui-bg-elevated);
      color: var(--ui-text);
      pointer-events: none;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
      left: -20px;
      width: 0.5rem;
      border-radius: 50%;
      font-weight: 400;
    }

    /* ── Focus ring (for keyboard navigation) ───────────────────── */
    .ms-mark:focus-visible {
      outline: 2px solid #111;
      outline-offset: 2px;
    }

  `;

  document.head.appendChild(style);
}
