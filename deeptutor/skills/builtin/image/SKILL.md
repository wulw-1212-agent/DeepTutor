---
name: image
description: Read and describe image files in the workspace - photos of worksheets and exercises, screenshots, diagrams, charts and figures. Use whenever the user attaches or points at an image and you need its actual content, such as the printed question, the handwritten answers, or the labels on a figure. Without this you only see the file path, not the image.
tags:
- tool
- vision
requires:
  sandbox: shell
---

# Image

`describe_image` shows you what an image contains. The model behind it can
read the image, so ask it exactly what you need.

## When to use it

The moment the user attaches or names an image, call this tool on it. A path
alone tells you nothing about the content - the file name is usually a hash.

Do not answer "the image is stored as a note asset, I cannot see it". You can
see it. Use the tool.

## Images inside a notebook page

This is the common case: the user pastes photos into a notebook page and asks
you to work from them. You do NOT need to copy anything into the workspace,
and you do NOT need the user to re-upload anything.

1. `search_notes(query="<page title>")` to find the page, then
   `search_notes(page_id="<id>")` to read its text. The text shows the image
   references, e.g. `![image.png](/api/documents/<doc-id>/assets/<file>.png)`.
2. For EACH of those references, call the `describe_image` tool with that
   reference as the `path`. The path in the page body is exactly what the tool
   accepts - do not resolve it, do not strip it, do not copy it, do not move
   the file, and do not try to read the bytes yourself.
3. Work from the returned descriptions to build the deliverable.

So the flow is: read the page, call `describe_image` on every referenced
image, then produce. Six photos means six `describe_image` calls - one per
image, in the same turn.

## Call it

Call the `describe_image` TOOL directly. Do not use `exec`, `workspace_read`,
`workspace_list` or `workspace_search` to locate the file, and do not copy it
anywhere - the tool takes the path exactly as it appears in the note.

```
describe_image(
    path="/api/documents/452dc6c8013b/assets/94506171431741e4affaed792729e4f7.png",
    question="What is the heading, and what arithmetic is written on the page?"
)
```

- `path` - the image file. All of these work:
  - workspace-relative, as `workspace_list` / `workspace_search` report it
    (`co-writer/documents/<doc>/assets/<file>.png`);
  - a note's asset URL path as it appears in the note body
    (`/api/documents/<doc-id>/assets/<file>.png`) - pass it without the
    `/api` prefix or with it, both resolve;
  - a chat attachment under `chat/attachments/<session>/<file>.png`.
- `question` - optional, and worth using. A targeted question beats a generic
  caption: ask for the printed question, for the handwritten answers, for the
  axis labels, for what the diagram shows.

The result is the model's reading of the image: use it as your source of truth
for anything you then write, quote, or grade.

## Reading several images

Call the tool once per image, then work from the descriptions. Worksheets are
often several pages: one call per page, then merge them into one document.

## Images vs scanned documents

- **An image file** (`.png`, `.jpg`, `.webp`, `.gif`): use `describe_image`.
- **A scanned PDF**: use the `pdf` skill - its OCR path extracts the literal
  text layer, which is faster and exact for printed pages.
- **A photo of a worksheet**: `describe_image` also reads handwriting, layout
  and diagrams, so prefer it when the page has hand-written answers.

## Limits

- PNG, JPEG, WEBP, GIF, BMP and TIFF are supported; other formats are rejected.
- Very large images are downscaled to a readable size before the model sees them.
- If the file cannot be found or decoded, the tool says so plainly - report
  that instead of guessing at the content.