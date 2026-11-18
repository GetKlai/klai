---
paths: ["klai-website/src/content/**/*.md", "klai-website/src/content/**/*.mdoc", "klai-website/.claude/agents/gtm/**/*.md"]
---

# Humanizer: Remove AI Writing Patterns

Apply this protocol to all content before publishing. Remove signs of AI-generated text, keep what the text says.

AI text sounds the way it does because a model picks the choice that fits the widest range of readers. A writer picks for one reader and one subject. Every pattern below is one form of that default choice. Every sentence you keep must add something the reader did not already have. Patterns 1 to 5 justify an edit on one sighting; a pattern marked *weak alone* needs other tells in the same passage.

## How to work

This protocol filters a draft that already exists and is not itself a way to write. Nothing in it licenses deleting a connective, replacing a comma with a full stop, or flattening a paragraph into a row of short sentences. If the rewrite reads as a telegram, pattern 2 applies to your own output as much as to the original.

1. **Mark the tells.** Read the whole text once, strongest first. Look at paragraph shape too: a contrast split over two sentences, three parallel examples, or the same closer after every section is the same tell at a larger scale.
2. **Rewrite.** Keep every supported claim; shorten dull parts, merge or split paragraphs.
3. **Check.** Did the rewrite drop or change a fact, name, number, date, quote or ranking? That is an error unless a pattern calls for the cut. Then re-scan for the tells that survive most often: 1, 2, 6, 8 and 19.
4. **Add no facts.** Do not add a detail, number or example that the draft or its source does not already contain. Where the text is generic, cut the generic claim or flag the gap for the writer (the proof rule in `klai-brand-voice.md` decides what may stay).

Leave a watched phrase alone inside a quotation, a title, a proper name, or a passage that discusses the phrase. Keep details that carry the writer's voice: a specific unusual detail, mixed feelings, a genuine aside or self-correction.

## A. Staging instead of stating

### 1. Not X but Y
**Watch for:** not X but Y; not just / not only / not merely X, but Y; it's not X, it's Y; X rather than Y; the same contrast split across sentences ("This does not mean X. It means Y."); a clipped negative tail ("..., no guessing")
**Fix:** State the point directly. Keep a contrast only when the negative half corrects a belief the reader actually holds.

### 2. One-line closers and dramatic fragments
**Watch for:** a one-sentence paragraph that restates the one before; "That is the real win."; "That distinction matters."; "Let that sink in."; the same closer after several sections; a sentence that explains an example the reader just saw
**Fix:** Cut a closer that repeats. Keep a short sentence only when it carries a new fact.

### 3. Sayings that sound deep
**Watch for:** the real question is, at its core, what really matters, fundamentally, the heart of the matter, X is the Y of Z, X becomes a trap, the language of, the currency of
**Fix:** Replace the saying with the specific claim.

### 4. Staged run-up before the point
**Watch for:** let's dive in, here's what you need to know, without further ado, Honestly?, Look, Here's the thing, Let's be honest, Real talk
**Fix:** Remove the run-up. A casual "honestly" inside a sentence is fine; the tell is the standalone opener before a routine claim.

### 5. Arguing with no one
**Watch for:** This isn't about, I'm not saying, To be clear, Don't get me wrong, Some might say... but, A tempting approach would be, You might think... but
**Fix:** Remove the defense or the fake alternative; if it holds a real claim, state the claim. Keep an objection the text attributes or answers in full.

## B. Rhythm by rule

### 6. Forced triads
**Pattern:** ideas in threes to sound complete: "innovation, inspiration, and industry insights", three parallel examples, three short facts and a lesson
**Fix:** Keep the items the meaning has. Two or four is fine. Check that no fact was lost.

### 7. Repeated sentence openings
**Pattern:** several sentences in a row start with the same subject
**Fix:** Merge them, change the subject, or begin with the action. A deliberate repeat stays.

### 8. Dashes as the universal connector
**Fix:** No em or en dashes in the final text: use a comma, colon, parentheses or a new sentence, whichever fits the relation between the clauses. One dash is *weak alone*; a text full of them is not.

### 9. Stacked qualifiers and filler
**Pattern:** "could potentially possibly be argued", "to be fair", "it's also possible"; filler such as "in order to", "due to the fact that", "at this point in time", "the system has the ability to", "it is important to note that"
**Fix:** "The policy may affect outcomes." / "To", "Because", "Now", "can"; remove the last one entirely. Keep a qualifier only when the source supports it and the meaning needs it.

### 10. Hyphenated pairs everywhere
**Watch for:** high-quality, well-documented, long-term, real-time after the noun they describe
**Fix:** Keep the hyphen before a noun ("a high-quality report"), drop it after ("the report is high quality"). Words always spelled with a hyphen keep it.

### 11. Passive voice and missing subjects
**Fix:** Use the active voice where it makes the actor clear. *Weak alone.*

## C. Inflation and borrowed authority

The fact underneath is usually sound. Keep it and remove the dressing.

### 12. Overused AI words
**Remove or reduce:** actually, additionally, align with, crucial, deep dive, delve, enduring, enhance, fostering, garner, gate/gated (figurative), highlight (verb), interplay, intricate/intricacies, key (adjective), landscape (abstract noun), pivotal, showcase, tapestry, testament, underscore, valuable, vibrant
**Fix:** Use the plain word. One such word outside a group is *weak alone*.

### 13. Inflated significance
**Watch for:** stands as a testament, a pivotal/crucial moment, plays a key role, underscores its importance, reflects a broader, enduring/lasting legacy, setting the stage for, evolving landscape, indelible mark; "Despite these challenges... continues to thrive"; "The future looks bright", "Exciting times lie ahead"
**Fix:** Keep the fact, drop the significance. Cut a stock challenges-and-outlook section or a bright-future closing paragraph; end on the last concrete fact.

### 14. Vague connection or association
**Watch for:** associated with, in connection with, linked to, tied to
**Fix:** Name the relationship (founded, leads, advises). If it is unknown, keep the vague wording.

### 15. Shallow -ing phrases
**Watch for:** highlighting, underscoring, emphasizing, ensuring, reflecting, symbolizing, contributing to, fostering, encompassing, showcasing
**Fix:** Keep the fact; drop the -ing phrase unless the source supports what it claims.

### 16. Sales language
**Watch for:** boasts a, rich (figurative), profound, exemplifies, commitment to, nestled, in the heart of, groundbreaking, renowned, diverse array, breathtaking, stunning, must-visit
**Fix:** State what the thing is, with specific facts.

### 17. Borrowed authority
**Watch for:** experts argue, observers have cited, industry reports, some critics, several sources; a list of outlets that "featured" someone; "active social media presence"
**Fix:** Name the real source, with date, and what it said. Otherwise cut the claim or the list.

### 18. Avoiding is, are and has
**Watch for:** serves as, stands as, functions as, marks, represents; boasts, features, offers
**Fix:** Use "is", "are", "has".

## D. Formatting by rule

### 19. Bold as decoration
**Pattern:** bold on words with no reason; vertical lists where every item has a bold label and a colon ("* **User Experience:** The user experience has been...")
**Fix:** Bold only what is genuinely critical. Turn a labeled list into prose when the labels carry no information of their own.

### 20. Decorative headings
**Pattern:** Title Case headings; emojis or arrows in headings and bullets; a horizontal rule between every section; an opening heading that repeats the title
**Fix:** Sentence case only: "Strategic negotiations and global partnerships". No emojis in headings or bullets; at most 1-2 per article, in body text.

### 21. Curly quotation marks
**Fix:** Use straight quotes where the target format does. *Weak alone.*

## E. Leftovers from the chat and the draft

Remove these outright.

### 22. Chatbot residue
**Watch for:** I hope this helps, Of course!, Certainly!, Great question!, You're absolutely right, Would you like..., let me know
**Fix:** Remove the wrapper, keep the content.

### 23. Knowledge-limit disclaimers and guesses
**Watch for:** as of my last training update, while specific details are limited, based on available information, not publicly available, "maintains a low profile" followed by a plausible guess
**Fix:** State what the source does not show, or remove the sentence.

### 24. A heading repeated in the first sentence
**Pattern:** a heading followed by a one-line paragraph that restates it
**Fix:** Remove the repeated sentence.

### 25. Writing about the document instead of its subject
**Watch for:** what the text replaced ("was added to replace"); how it was sourced ("compiled from", "anything unconfirmed is flagged"); a legend or order the reader can already see
**Fix:** Mention a previous version only in change logs and release notes. Keep a source credit the reader can follow; cut the account of how you worked.

## F. Writing for the wrong reader

### 26. Re-explaining what the reader knows
**Pattern:** a short reply that restates the problem and the diagnosis before it reaches the decision
**Fix:** Lead with the decision and keep only the reasoning that would change whether the reader agrees. Applies to replies, not standalone articles.

## G. Klai's own pattern

### 27. Fake Contrast Emphasis
**Pattern:** "That is not X. That is Y." or "This is not a preference. This is a fact." where X and Y are not actually in conflict. Used to make a statement sound more dramatic or authoritative than it is. (Pattern 1 covers the general contrast; this is the two-sentence staged form.)
**Examples to remove:**
- "That is not a preference. That is the measured behavior of the architecture."
- "This is not optional. This is how it works."
- "Not just X. Y."
**Fix:** Make a single direct statement. "The model pays more attention to the beginning and end of a document", full stop. No fake drama required.

## Adding Soul (Anti-Soulless Writing)

After removing AI patterns, add human voice. This is about voice, not about new facts (see "How to work", step 4):

- **Have opinions**: React to facts, don't just report them
- **Vary rhythm**: Short punchy sentences. Then longer ones that take their time.
- **Acknowledge complexity**: "This is impressive but also kind of unsettling"
- **Use "ik" when it fits**: "Ik kom hier steeds op terug..." signals a real person
- **Be specific about feelings**: Not "this is concerning" but describe the specific unease
- **Let some mess in**: Perfect structure feels algorithmic

---

## Output

- Return only the edited article in Markdown, no commentary
- Match the language of the input: NL input -> NL output, EN input -> EN output

---

*Last updated: 2026-11-18. Pattern catalogue follows humanizer v4.0.0.*
