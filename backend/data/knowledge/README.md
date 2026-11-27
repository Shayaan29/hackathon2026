# Manuals the AI reads

Put the manuals of your sensors here: PDF, text (.txt) or Markdown (.md). The cockpit reads them
when it starts, and you can also add or remove them in the cockpit under **Manuals**.

* The AI quotes these pages when it recommends what to check. A quote is shown only if it is word for word in the manual.
* PDFs need selectable text. A scan without text cannot be read.
* The first start after adding a large PDF takes a few seconds; the text is then kept in `.cache/`.
* Everything in this folder except this note is ignored by git. The manuals are the manufacturer's property, so they stay
  on your computer. Never force-add them to the public repository.
* The cockpit reads the folder when it starts. Files added under **Manuals** are saved here and work at once.
* The text of the pages that match a question is sent to OpenAI to find and draft the answer. If a document is marked
  confidential, ask the organisers first.

Where to get them: the product page of each sensor on micro-epsilon.com, under Downloads, the "Operating Instructions" PDF.
