# Obsidian Web Clipper Templates

This folder keeps importable templates for the official Obsidian Web Clipper.

## Default Template

Use `line-inspiration-web-clip.json` for general webpages.

Settings:

- Vault: `ObisdianVault`
- Folder/path: `Sources/web-clips`
- Behavior: create a new note
- Capture status: `full`
- Extractor: `web-clipper`
- Needs review: `true`

The template intentionally does not create an AI summary. It saves the page
content and leaves a review checklist so failed or incomplete captures do not
look like digested knowledge.

## Selection / Term Template

Use `line-inspiration-selection-clip.json` when you only want to capture a
selected paragraph, definition, or term explanation.

Settings:

- Folder/path: `Sources/web-clips/terms`
- Capture status: `partial`
- Extractor: `web-clipper-selection`
- Needs review: `true`

Workflow:

1. Select the paragraph or term explanation on the page.
2. Open Obsidian Web Clipper.
3. Choose `Line Inspiration Selection Clip`.
4. Save the page.

This template uses `{{selection}}`, so it is for manually selected content, not
the whole webpage.

## Import

1. Open the Obsidian Web Clipper extension.
2. Open Settings.
3. Go to Templates.
4. Click Import.
5. Select one of the JSON files in `web_clipper_templates/`.
6. Move `Line Inspiration Web Clip` above the default template if you want it to trigger for
   normal `http` and `https` pages.
