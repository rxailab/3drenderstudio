# What the example listing images taught us

A study of 82 product images from 20 Amazon US tool listings (two sets supplied as examples; 19 listings have four images, one has six), and what the studio was changed to do because of it. Every image was opened and described one by one; the counts below come from those notes.

The images themselves are not in the repository. They are other companies' listings and were used only to learn from. Nothing of theirs (names, logos, artwork, wording) is built into the product.

## The conclusion in one paragraph

A listing image is rarely a photograph of a product. It is a **designed page assembled from several pictures**, filled from product facts, and held together across the whole listing by a brand system. The studio made one picture of one model with one layer of words on it, so most of the examples were out of reach however good the render was: of the 82 images it could make 9 as they are, 34 in part and 39 not at all. The gap was the unit of work, not render quality. Version 29 changes the unit: a project now has components, a product record and a brand kit, and builds a set of pages from them.

## What the 82 images are

| Kind of image | Count | What it does for the buyer |
| --- | --- | --- |
| White-background main image | 20 | Shows what you get. 14 of the 20 are kits: the tool large, its accessories around it |
| In-use scene with a person or hands | 14 | Shows it working |
| Callout diagram | 11 | Names the parts and what they do |
| Kit page ("kit includes") | 8 | Lists what is in the box, with model numbers |
| Single-feature close-up | 5 | Makes one claim, large |
| Text-free alternate view | 4 | Another side of the product |
| Application grid | 4 | The jobs it does, one photo per job |
| Staged scene without people | 3 | The product in its place |
| Comparison | 3 | Ours against the alternative |
| How-to steps | 2 | How to use it |
| Compatibility diagram | 2 | What it fits |
| Specification chart | 2 | Numbers in a table |
| Gift scene, cutaway, collage, cross-sell | 1 each | |

A listing is a sequence of arguments, in roughly this order: what you get, what is in the box, what the parts do, what it is like in use, why it is better, how big it is. One claim per image.

## How the images are built

**Ground.** 30 are white with cut-outs. 9 are white inside solid bands: a dark header with an accent stripe and a badge, a white body, a dark footer with a legal line. 16 are full-bleed scenes, 4 a photo above a solid band. 9 are dark textured grounds, 6 a flat brand colour.

**Arrangement.** Five templates cover two thirds of the set:

1. A scene with a headline laid over it, often with chips or a translucent panel (15).
2. The hero tool very large on white with its accessories spread around it (14).
3. A product cut-out with leader-line callouts (12).
4. A grid of captioned tiles: kit contents, use cases, steps (11).
5. One product filling a white frame, no text (10).

Long tools sit on a diagonal. Products ignore margins and run off the edge of the image; text keeps a margin of 3 to 6 per cent. There is one headline and at most one line under it.

**Type.** All sans-serif, headlines always heavy. Three widths are in use: a wide heavy grotesque (the big-brand kit pages), heavy condensed capitals (promotional pages), and a heavy face of normal width. Smaller brands add square display faces, italics, outlines, gradient fills and drop shadows. In any block of figures the number is the largest thing. Two-tone headlines put one phrase in the brand colour.

**Elements.** Leader lines in one fixed style per brand (for example right-angle accent lines with a square end). A brand badge in a corner on 12 images. Rows of three to five icons. Pills, two-tone chips, translucent panels. Circular inset close-ups. Value badges and piece-count flags. Ticks and crosses, before-and-after plates, one real table, chevrons and arrows, dimension lines once.

**Colour.** One accent per listing, nearly always the product's own colour, with black, white and grey.

**People.** 15 images show a person, 7 only hands, 60 nobody. When a person appears it is one person at work, eyes on the work, cropped at the chest or waist, with evidence of the work at the point of contact: sparks, sawdust, water, torn card. The tool, the hand and the thing being worked on carry the picture; the face adds nothing.

## Big brands and small brands do it differently

| | Big brands | Small brands |
| --- | --- | --- |
| Pictures | Real photography of the real tool, worn and dusty | Scenes that look composited or generated |
| Layout | One frame reused on every listing | A new layout for each image |
| Words | 3 to 5 labels, about 25 words per image | Up to 90 words, frequent spelling slips |
| Decoration | No icons, no effects | Icons, pills, outlines, glows |

Both work. The first reads as confident, the second as busy. The first is also easier to automate, because it is a system.

## What needs a person or a place, and what does not

55 of the 82 images can be made from a clean model or packshot alone: the white-ground images, kit pages, callout diagrams, close-ups, the cutaway, lists, diagrams. The other 27 depend on a person, hands, a real or generated scene, or photographs of other things (a competitor's product, a laptop being opened, a rusted bolt).

## Why the studio could not make them (before version 29)

1. **One project held one model, and one canvas held one picture.** Kit mains, kit grids, application grids, comparisons, steps and insets all need several pictures on one canvas. This alone blocked 39 images.
2. **There was no product record.** Kit pages, tables and figures are filled from facts: what is in the box, model numbers, voltage, capacity. There was nowhere to enter them.
3. **The text layer had seven kinds of item and one typeface.** No pictures as items, no frame, no table, arrows, badges or dimension lines; four callouts at most; no condensed or wide face, no italics, outlines or shadows.
4. **The product was always whole, inside a margin.** No bleed, no macro crop on a flat colour.
5. **One scene per project**, with no hands-only close-up.
6. **Images were planned by camera angle, not by message.** Six views of one object, where a listing wants six arguments.
7. **There was no brand kit.** A theme and an accent were stored per image.
8. **The model is the ceiling for the product itself.** Printed labels, moulding seams, a fabric bag with folds: a CAD export has none of these.

## What changed in version 29

| Finding | What the studio does now |
| --- | --- |
| Several pictures on one canvas | **Components**: each thing in the box is uploaded as a model (analysed, stood upright, rendered to a cut-out) or as a picture (cut out of its plain ground). Pages place any number of cut-outs |
| Facts fill the pages | **Product record**: name, model number, headline, features tied to parts of the model, specifications, key figures, real size. Drafted from the model's names and your notes, by the copy model when one is configured |
| A brand system | **Brand kit**: five presets taken from the families above (band frame, flat colour, dark tech, clean white, bold promo), one accent taken from the product's colour unless you set it, badges, a legal line, a logo |
| Wider vocabulary | **Element library**: 21 elements, among them frames, badges, chips, insets, leader lines in three manners, dimension lines, tables, key figures, tick lists, icon tiles, arrows, chevrons, bursts, corner flags |
| Three widths of heavy type | Archivo (width and weight axes, upright and italic) and Orbitron are bundled under the SIL Open Font License, beside Inter and Noto Sans SC |
| Plan by message | **Eight page kinds**: main image, kit includes, feature callouts, use case, feature close-up, headline band, benefits, specifications. The planner picks up to seven from what the record holds |
| Hands, not faces | A generated scene now shows a person as **hands at work, close up**, unless you ask to see the person; a picture that shows a face is made again |
| Dimension lines (rare in the set, asked for separately) | **Use-case page**: the product on a parametric workpiece (pipe, rod, board, sheet, block) with the workpiece's size, the product's measured size and its features marked |

By kind of image, the set now reads: main images including kit mains (20), kit pages (8), callout diagrams (11), close-ups (5), alternate views (4) and specification charts (2) have a page or a shot of their own, 50 in all. In-use and staged scenes (18) are made by the scene generator as before, still one per project. Application grids, comparisons, how-to steps, compatibility diagrams, the collage, the cutaway and the cross-sell page (14) have no template.

## What is still out of reach

- **The product can only look as real as its model.** No printed label, no fabric bag, no coiled cable unless the model has them. A real bag can be added as a photograph on a plain ground.
- **Comparisons are not generated.** A claim about a competitor cannot be checked from a model, so the studio does not write one.
- **Application grids and how-to steps** need several different photographs; one scene per project remains the limit.
- **Kit main images are arranged by rule**: the case behind, the product in front, small things in a row. A stylist would overlap and angle them with more care.
- **Workpiece placement is geometric.** A tool that closes round its work (a cutter, a wrench, a clamp) is found automatically; for a drill or a saw the placement is a starting point to adjust.
- **None of the 20 example products was rebuilt in the studio.** The claim is that these kinds of image can now be produced, shown on test models of an angle grinder kit and a pipe cutter; not that an example was matched pixel for pixel.

## How sure this is

- Type counts and template assignments involve judgement at the edges; another reader would move a few images between neighbouring kinds.
- Whether a scene in the examples is a photograph, a composite or generated is an impression from the picture.
- "Could be made" throughout assumes a model of the product exists and is detailed enough.
