<script setup>
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

import { onBeforeUnmount } from "vue";
import { useEditor, EditorContent } from "@tiptap/vue-3";
import { Node, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Collaboration from "@tiptap/extension-collaboration";
import Highlight from "@tiptap/extension-highlight";
import * as Y from "yjs";

const PageNode = Node.create({
  name: "page",
  group: "block",
  content: "block+", // Permite párrafos, listas, etc. dentro de la página
  defining: true, // Ayuda a mantener la estructura al pegar texto

  addAttributes() {
    return {
      number: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-number"),
        renderHTML: (attributes) => ({ "data-number": attributes.number }),
      },
      id: {
        default: null,
        parseHTML: (element) => element.getAttribute("id"),
        renderHTML: (attributes) => ({ id: attributes.id }),
      },
      class: {
        default: "page-virtual",
        parseHTML: (element) => element.getAttribute("class"),
        renderHTML: (attributes) => ({ class: attributes.class }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'div[data-type="page"]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes({ "data-type": "page" }, HTMLAttributes), 0];
  },
});

const route = useRoute();
const initialDocumentId = route.params.id;

const editorStore = useEditorStore();

const ydoc = new Y.Doc();

const extensions = [
  StarterKit.configure({
    undoRedo: false,
    history: false,
    bold: {
      HTMLAttributes: {
        class: "custom-bold-style",
      },
    },
  }),
  PageNode,
  Collaboration.configure({
    document: ydoc,
    field: "default",
  }),
  MultiSelect,
  Highlight,
];

const editor = useEditor({
  content: null,
  extensions,
  editorProps: {
    attributes: {
      spellcheck: "false",
      class: "prose-container",
    },
  },
});

watch(
  () => editorStore.message,
  (msg) => {
    if (!editor.value) return;

    if (msg?.documentId && msg.documentId !== initialDocumentId) {
      return;
    }

    if (msg.command === "get_document") {
      const binary = msg?.data?.content;

      if (binary) {
        Y.applyUpdate(ydoc, msg.data.content, "initial-load");
      }

      const chunks = msg?.data?.chunks;
      if (chunks) {
        chunks.forEach((chunk) => {
          Y.applyUpdate(ydoc, chunk.data, "initial-load");
        });
      }
    }
  },
  { immediate: true },
);

let localBuffer = [];

ydoc.on("update", (update, origin) => {
  if (origin === "initial-load") {
    console.log("Ignoring initial-load");
    return;
  }

  if (origin === "first-upload") {
    console.log("Sending first-upload");
  }

  localBuffer.push(update);
});

const selectionCount = computed(() => {
  if (!editor.value) return 0;
  return editor.value.storage.multiSelect.getSelectionCount(
    editor.value.state.doc,
  );
});

const logSelections = () => {
  if (!editor.value) return;

  const entries = editor.value.storage.multiSelect.getSelectionsInOrder(
    editor.value.state.doc,
  );
  console.log("Selecciones actuales:", entries);
};

let isProcessing = false;

async function processChanges() {
  // Exit if there are no updates to send or if a sync process is already in progress
  if (localBuffer.length === 0 || isProcessing) {
    return;
  }

  // 1. Lock the process to prevent concurrent execution (race conditions)
  isProcessing = true;

  const documentId = initialDocumentId;

  /**2. Extract and clear the buffer IMMEDIATELY.
   * New incoming updates during the async 'await' period will be pushed to a
   * fresh array, preventing data loss during the current transmission.
   */
  const updatesToSend = [...localBuffer];
  localBuffer = [];

  try {
    // 2. Consolidate all micro-deltas into a single compressed binary.
    const mergedUpdate = Y.mergeUpdates(updatesToSend);

    // 3. Send a SINGLE network request to the backend.
    const result = await editorStore.send({
      command: "edit_document",
      params: {
        document_id: documentId,
        binario: mergedUpdate,
        page: "default",
      },
    });

    if (!result) {
      throw new Error("El servidor no confirmó la recepción");
    }
  } catch (error) {
    console.error("Error enviando cambios, reintentando...", error);

    /**
     * Network failure recovery:
     * Prepend the failed updates back to the beginning of the buffer so they
     * can be merged and re-attempted in the next interval.
     */
    localBuffer.unshift(...updatesToSend);
  } finally {
    // Release the lock to allow the next scheduled process to run
    isProcessing = false;
  }
}

const miIntervalo = setInterval(processChanges, 1000);

function cerrarDocumento() {
  clearInterval(miIntervalo);

  // Opcional: Si quedó algo en el buffer justo antes de cerrar,
  // puedes intentar hacer un último envío forzado aquí.
  if (localBuffer.length > 0) {
    processChanges();
  }
}

onBeforeUnmount(() => {
  cerrarDocumento();

  if (editor.value) {
    editor.value.destroy();
  }

  ydoc.destroy();
});
</script>

<template>
  <div class="editor-layout">
    <div v-if="editor" class="editor-header">
      <UButton
        icon="lucide:undo"
        size="xs"
        color="neutral"
        variant="ghost"
        :disabled="!editor.can().undo()"
        @click="editor.chain().focus().undo().run()"
      />

      <UButton
        icon="lucide:redo"
        size="xs"
        color="neutral"
        variant="ghost"
        :disabled="!editor.can().redo()"
        @click="editor.chain().focus().redo().run()"
      />

      <USeparator orientation="vertical" class="h-6" />

      <UButton
        class="rounded-sm"
        :class="{ 'is-active': editor.isActive('bold') }"
        icon="lucide:bold"
        size="xs"
        color="neutral"
        :variant="editor.isActive('bold') ? 'outline' : 'ghost'"
        @click="editor.chain().focus().toggleBold().run()"
      />

      <UButton
        class="rounded-sm"
        :class="{ 'is-active': editor.isActive('italic') }"
        icon="lucide:italic"
        size="xs"
        color="neutral"
        :variant="editor.isActive('italic') ? 'outline' : 'ghost'"
        @click="editor.chain().focus().toggleItalic().run()"
      />

      <UButton
        class="rounded-sm"
        :class="{ 'is-active': editor.isActive('highlight') }"
        icon="lucide:highlighter"
        aria-label="Resaltador (Ctrl+Shift+H / Cmd+Shift+H)"
        title="Resaltador · Ctrl+Shift+H (Cmd+Shift+H en Mac)"
        size="xs"
        color="neutral"
        :variant="editor.isActive('highlight') ? 'outline' : 'ghost'"
        @click="editor.chain().focus().toggleHighlight().run()"
      />

      <UButton
        class="rounded-sm"
        :class="{ 'is-active': editor.isActive('test') }"
        icon="i-lucide-list"
        size="xs"
        color="neutral"
        variant="ghost"
        @click="editor.chain().focus().toggleHighlight().run()"
      />

      <UButton
        class="rounded-sm"
        :class="{ 'is-active': editor.isActive('test') }"
        icon="i-lucide-code"
        size="xs"
        color="neutral"
        variant="ghost"
        @click="editor.chain().focus().toggleHighlight().run()"
      />

      <USeparator orientation="vertical" class="h-6 ml-auto" />

      <UButton
        :class="{ 'is-active': editor.isActive('multiSelect') }"
        icon="lucide:mouse-pointer-click"
        size="xs"
        label="Select"
        color="primary"
        :variant="editor.isActive('multiSelect') ? 'subtle' : 'ghost'"
        @click="editor.chain().focus().toggleSelection().run()"
      />

      <UButton
        :class="{ 'is-active': selectionCount > 0 }"
        label="Clean"
        size="xs"
        icon="lucide:eraser"
        color="neutral"
        variant="ghost"
        :disabled="!selectionCount"
        @click="editor.commands.clearAllSelections()"
      >
      </UButton>

      <button style="display: none" @click="logSelections">ver</button>
    </div>

    <div class="editor-container">
      <editor-content :editor="editor" class="tiptap-viewport" />
    </div>

    <div class="footer">
      <UBadge
        class="flex items-center text-center rounded-2xl"
        color="neutral"
        variant="ghost"
        size="sm"
        >Selected {{ selectionCount }}</UBadge
      >

      <span class="ml-auto">
        <UBadge
          class="flex items-center text-center"
          color="neutral"
          variant="ghost"
          size="sm"
          >Page 23 of 253</UBadge
        >
      </span>
    </div>
  </div>
</template>

<style>
/* Contenedor principal para organizar Header y Editor */
.editor-layout {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 2.5rem); /* Ocupa todo el alto de la ventana */
  overflow: hidden; /* Evita scroll doble */
}

.editor-header {
  gap: 0.5rem;
  z-index: 10;
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--ui-border);
  border-bottom-left-radius: calc(var(--ui-radius) * 0);
  border-bottom-right-radius: calc(var(--ui-radius) * 0);
}

/* --- CAPA DE RENDIMIENTO (CRÍTICO) --- */
.editor-container {
  overflow-y: auto; /* Activa el scroll vertical cuando el contenido sea mayor a 100vh */
}

/* 2. Motores WebKit (Chrome, Safari, Edge) */
.editor-container::-webkit-scrollbar {
  width: 1rem; /* Ancho del scroll vertical */
  height: 1rem; /* Ancho del scroll horizontal */
}

.editor-container::-webkit-scrollbar-track {
  background: transparent;
}

.editor-container::-webkit-scrollbar-thumb {
  border-radius: 8px;
  background: var(--ui-bg-accented);
  border: 0.25rem solid var(--ui-bg);
  height: 5rem;
}

.editor-container::-webkit-scrollbar-thumb:hover {
  background: var(--ui-bg-inverted);
}

.tiptap-viewport {
  max-width: 900px;
  margin: 0 auto;
  padding: 2rem;
}

.tiptap-viewport .ProseMirror {
  min-height: 100%;
  outline: none;
}

.page-virtual {
  /* La magia: el navegador no dibuja la página si no está en el viewport */
  content-visibility: auto;

  /* Evita que el scrollbar "tiemble". Ajusta 1100px a la altura real de tu página */
  contain-intrinsic-size: 1px 1100px;

  padding: 60px 80px;
  position: relative;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0);

  display: block;
  content-visibility: auto;
  contain-intrinsic-size: 1px 1100px; /* Muy importante para el scrollbar */
  min-height: 100px;
  border-bottom: 1px solid var(--ui-border);
}

/* --- INDICADOR DE PÁGINA (ESTÉTICO) --- */

.page-virtual::before {
  /* Toma el número del atributo data-number automáticamente */
  content: "" attr(data-number);
  position: absolute;
  top: 20px;
  right: 30px;
  font-size: var(--text-xs);
  font-weight: 400;
  color: var(--ui-text-muted);
  letter-spacing: 1px;
  user-select: none; /* Evita que el número se seleccione al copiar texto */
  pointer-events: none; /* El ratón lo atraviesa para no estorbar la edición */
  opacity: 0;
}

/* Estilos de texto básicos para que se vea bien */
.prose-container:focus {
  outline: none;
}

.page-virtual p,
h1 {
  line-height: 1.6;
  margin-bottom: 1rem;
  opacity: 0.9;
}

.custom-bold-style {
  color: var(--ui-primary);
}

.footer {
  z-index: 10;
  gap: 0.5rem;
  display: flex;
  align-items: center;
  padding: 0rem 1rem;
  border-top: 1px solid var(--ui-border);
}
</style>
