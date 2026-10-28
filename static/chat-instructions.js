const chatInstructionsStorageKey = 'alhuzaifa.chat-instructions';
const instructionsToggle = document.querySelector('#edit-chat-instructions');
const instructionsDialog = document.querySelector('#chat-instructions-dialog');
const instructionsForm = document.querySelector('#chat-instructions-form');
const instructionsText = document.querySelector('#chat-instructions-text');
const instructionsFeedback = document.querySelector('#chat-instructions-feedback');
const instructionsSave = document.querySelector('#save-chat-instructions');
const instructionsDefault = document.querySelector('#default-chat-instructions');
let defaultChatInstructions = null;
let customChatInstructions = null;

try {
  const saved = localStorage.getItem(chatInstructionsStorageKey);
  if (saved?.trim() && saved.length <= instructionsText.maxLength) customChatInstructions = saved.trim();
} catch { /* Changes still work for this tab when browser storage is unavailable. */ }

function getChatInstructions() {
  return customChatInstructions;
}

function updateInstructionsLabel() {
  instructionsToggle.textContent = customChatInstructions ? 'Chat instructions · Custom' : 'Chat instructions';
}

async function openChatInstructions() {
  instructionsDialog.showModal();
  instructionsFeedback.textContent = '';
  instructionsText.value = customChatInstructions ?? defaultChatInstructions ?? '';
  instructionsSave.disabled = instructionsDefault.disabled = defaultChatInstructions === null;
  if (defaultChatInstructions === null) {
    instructionsToggle.disabled = true;
    instructionsText.disabled = true;
    instructionsFeedback.textContent = 'Loading instructions…';
    try {
      const response = await fetch('/api/chat/instructions', { cache: 'no-store' });
      if (!response.ok) throw new Error('Unable to load instructions. Close this window and try again.');
      const data = await response.json();
      if (typeof data.chat_instructions !== 'string' || !data.chat_instructions.trim()) {
        throw new Error('Unable to load instructions. Close this window and try again.');
      }
      defaultChatInstructions = data.chat_instructions;
      instructionsText.maxLength = data.max_length;
      instructionsText.value = customChatInstructions ?? defaultChatInstructions;
      instructionsSave.disabled = instructionsDefault.disabled = false;
      instructionsFeedback.textContent = '';
    } catch (error) {
      instructionsFeedback.textContent = error.message;
    } finally {
      instructionsToggle.disabled = false;
      instructionsText.disabled = false;
    }
  }
  if (instructionsDialog.open) instructionsText.focus();
}

instructionsToggle.addEventListener('click', openChatInstructions);
document.querySelector('#cancel-chat-instructions').addEventListener('click', () => instructionsDialog.close());
instructionsDefault.addEventListener('click', () => {
  instructionsText.value = defaultChatInstructions;
  instructionsFeedback.textContent = 'Default instructions restored. Save to apply.';
});
instructionsForm.addEventListener('submit', (event) => {
  event.preventDefault();
  if (defaultChatInstructions === null) return;
  const value = instructionsText.value.trim();
  if (!value || value.length > instructionsText.maxLength) {
    instructionsFeedback.textContent = `Enter instructions between 1 and ${instructionsText.maxLength.toLocaleString()} characters.`;
    instructionsText.focus();
    return;
  }
  customChatInstructions = value === defaultChatInstructions.trim() ? null : value;
  try {
    if (customChatInstructions === null) localStorage.removeItem(chatInstructionsStorageKey);
    else localStorage.setItem(chatInstructionsStorageKey, customChatInstructions);
  } catch { /* Keep the applied instructions in memory for this tab. */ }
  updateInstructionsLabel();
  instructionsDialog.close();
});

updateInstructionsLabel();
