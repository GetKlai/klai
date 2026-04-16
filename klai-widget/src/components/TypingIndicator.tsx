// Animated typing indicator shown while streaming response
export function TypingIndicator() {
  return (
    <div class="klai-typing" role="status" aria-label="Assistant is typing">
      <div class="klai-typing-dot" />
      <div class="klai-typing-dot" />
      <div class="klai-typing-dot" />
    </div>
  );
}
