---
paths: ["klai-website/src/content/**/*.md", "klai-website/src/content/**/*.mdoc", "klai-website/.claude/agents/gtm/**/*.md"]
---

# Humanizer: Remove AI Writing Patterns

Apply this protocol to all content before publishing. Remove signs of AI-generated text.

## Content Patterns to Remove

### 1. Undue Emphasis on Significance
**Words to remove:** stands/serves as, is a testament/reminder, a vital/significant/crucial/pivotal/key role/moment, underscores/highlights its importance, reflects broader, symbolizing its ongoing/enduring/lasting, represents a shift, key turning point, evolving landscape, indelible mark

**Fix:** Replace puffed-up significance claims with specific facts.

### 2. Superficial -ing Endings
**Words to watch:** highlighting/underscoring/emphasizing..., ensuring..., reflecting/symbolizing..., contributing to..., cultivating/fostering..., encompassing..., showcasing...

**Fix:** Rewrite as separate sentences or remove entirely.

### 3. Promotional Language
**Words to remove:** boasts a, vibrant, rich (figurative), profound, enhancing its, showcasing, exemplifies, commitment to, nestled, in the heart of, groundbreaking, renowned, breathtaking, stunning

**Fix:** Replace with specific, factual descriptions.

### 4. Vague Attributions
**Words to watch:** Industry reports, Observers have cited, Experts argue, Some critics argue, several sources

**Fix:** Use specific sources with dates, or remove the attribution.

### 5. Challenges and Future Prospects Sections
**Pattern:** "Despite its... faces several challenges..., Despite these challenges..."

**Fix:** Integrate challenges naturally into the narrative with specifics.

## Language Patterns to Remove

### 6. Overused AI Vocabulary
**Remove or reduce:** Additionally, align with, crucial, delve, emphasizing, enduring, enhance, fostering, garner, highlight (verb), interplay, intricate/intricacies, key (adjective), landscape (abstract noun), pivotal, showcase, tapestry, testament, underscore, valuable, vibrant

### 7. Copula Avoidance
**Pattern:** "serves as / stands as / marks / represents [a]", "boasts / features / offers [a]"
**Fix:** Replace with "is" / "has" / "includes"

### 8. Negative Parallelisms
**Pattern:** "Not only...but...", "It's not just about..., it's..."
**Fix:** Make a direct statement instead.

### 9. Rule of Three Overuse
**Pattern:** Lists forced into groups of three: "innovation, inspiration, and industry insights"
**Fix:** Say only what needs to be said.

### 10. Elegant Variation (Synonym Cycling)
**Pattern:** protagonist -> main character -> central figure -> hero (all referring to same person)
**Fix:** Pick one term and use it consistently.

## Style Patterns to Remove

### 11. Em Dash Overuse
**Fix:** Replace em dashes with commas, periods, or parentheses where appropriate.

### 12. Overuse of Boldface
**Fix:** Bold only what is genuinely critical. When in doubt, remove.

### 13. Inline-Header Vertical Lists
**Pattern:** "* **User Experience:** The user experience has been..."
**Fix:** Integrate into prose or use simpler list format.

### 14. Title Case in Headings
**Fix:** Sentence case only,"Strategic negotiations and global partnerships" not "Strategic Negotiations And Global Partnerships"

### 15. Excessive Emojis in Copy
**Fix:** Remove all emojis from headings and bullet points. Allow max 1-2 per article in body text only.

### 16. Fake Contrast Emphasis
**Pattern:** "That is not X. That is Y." or "This is not a preference. This is a fact." — where X and Y are not actually in conflict. Used to make a statement sound more dramatic or authoritative than it is.
**Examples to remove:**
- "That is not a preference. That is the measured behavior of the architecture."
- "This is not optional. This is how it works."
- "Not just X. Y."
**Fix:** Make a single direct statement. "The model pays more attention to the beginning and end of a document" — full stop. No fake drama required.

## Communication Patterns to Remove

### 17. Collaborative Artifacts
**Pattern:** "I hope this helps", "Of course!", "Certainly!", "Would you like...", "let me know"
**Fix:** Remove entirely,these are chatbot phrases, not content.

### 18. Knowledge-Cutoff Disclaimers
**Pattern:** "as of my last training update", "While specific details are limited..."
**Fix:** Remove or replace with verifiable facts.

### 19. Sycophantic Tone
**Pattern:** "Great question!", "You're absolutely right!", "That's an excellent point"
**Fix:** Remove entirely.

## Filler and Hedging

### 19. Filler Phrases
- "In order to achieve this goal" -> "To achieve this"
- "Due to the fact that" -> "Because"
- "At this point in time" -> "Now"
- "The system has the ability to" -> "The system can"
- "It is important to note that" -> Remove entirely

### 20. Excessive Hedging
**Pattern:** "It could potentially possibly be argued that the policy might have some effect..."
**Fix:** "The policy may affect outcomes."

### 21. Generic Positive Conclusions
**Pattern:** "The future looks bright... Exciting times lie ahead... This represents a major step..."
**Fix:** Replace with specific next actions or concrete facts.

## Adding Soul (Anti-Soulless Writing)

After removing AI patterns, add human voice:

- **Have opinions**: React to facts, don't just report them
- **Vary rhythm**: Short punchy sentences. Then longer ones that take their time.
- **Acknowledge complexity**: "This is impressive but also kind of unsettling"
- **Use "ik" when it fits**: "Ik kom hier steeds op terug..." signals a real person
- **Be specific about feelings**: Not "this is concerning" but describe the specific unease
- **Let some mess in**: Perfect structure feels algorithmic

---

## Output

- Return only the edited article in Markdown,no commentary
- Match the language of the input: NL input -> NL output, EN input -> EN output

---

*Last updated: 2026-03-20*
