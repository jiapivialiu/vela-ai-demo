# Copy quality review

**Status**: `revise`
**Summary**: EN: Core specs (brand, model, wattage, included nozzles) are grounded in the image, but the listing is a raw draft with placeholder language, missing colour/finish details visible in the image, and no Canadian-market compliance signals (e.g., CSA/UL certification, bilingual requirement note). | FR: Listing is broadly accurate but contains minor formatting issues, untranslated English specs, a missing CCC certification visible on the unit, and draft/internal notes that must be removed before publication.

## Scores
- **grounding**: 0.62
- **factual_tone**: 0.55

## Exaggeration / hype findings

- No exaggerated claims detected; however, the draft description contains no marketing copy at all, which is a quality gap rather than an exaggeration.
- No exaggerated claims detected; promotional phrases from the original image ('genuine product', 'free shipping', 'trusted by professional stylists') were correctly omitted.

## Conflicts with structured attributes

- param_power is listed as '1900Watts' (no space, inconsistent formatting); image clearly shows '1900W' and '1900 Watts' — standardise to '1900 W' per Canadian convention.
- param_colour is 'Not specified' but the image clearly shows a black hair dryer body — should be 'Black'.
- param_certifications_visible is 'Not specified' but the image shows a CCC (China Compulsory Certification) mark on the dryer body — this should be noted and a Canadian-market certification (CSA/cUL) absence should be flagged for human review.
- param_included_accessories lists '2 styling nozzles' which matches the image (two concentrator nozzles shown), but the listing title and description do not mention this accessory — inconsistency between key_attributes and body copy.
- param_power is written as '1900Watts' (no space, mixed format) — should be '1900 W' following SI convention used in Canadian French listings.
- '2 styling nozzles included' in size_or_specs was translated to '2 buses de concentration' in param_included_accessories but left in English ('2 styling nozzles included') inside the description body — inconsistent language within the same listing.
- param_certifications_visible is 'Non précisé' but the product image clearly shows a CCC mark on the dryer body — this should be noted as 'CCC (visible sur l image)' with a caveat that Canadian safety certification (CSA/UL/cUL) is not confirmed.

## Image / visual mismatches

- Colour: image shows a glossy black dryer; listing says 'Not specified' for colour.
- CCC certification badge is visible on the dryer body but is not captured in param_certifications_visible.
- Product box (KANGFU branded retail packaging) is visible in the image but not referenced anywhere in the listing — could support 'retail box included' attribute.
- Two concentrator nozzle attachments are clearly shown separately in the image and are correctly captured in attributes, but are absent from the title and description body.
- CCC certification mark is visible on the dryer body in the image but is not reflected anywhere in the listing attributes.
- Product colour is visibly black in the image; param_colour is 'Non précisé' — should be updated to 'Noir'.
- Two concentrator nozzles are clearly shown in the image and are correctly captured, but the description body leaves the accessory line in English rather than French.
- The product box (KANGFU branding) is visible in the image and confirms brand/model — no mismatch there.
- No dimensions or weight are visible in the image, so 'Non précisé' for those fields is acceptable.

## Suggested fixes (EN)

• Replace placeholder description with verified marketing copy before publishing.
• Update title to something descriptive, e.g., 'KANGFU 8881 Professional Hair Dryer, 1900 W, Black — Includes 2 Styling Nozzles'.
• Set param_colour to 'Black' based on image evidence.
• Standardise power format to '1900 W' (space between number and unit) per Canadian SI convention.
• Add param_certifications_visible value: 'CCC (visible on unit); CSA/cUL certification not confirmed — verify before listing on Canadian marketplace'.
• Add a note about retail box inclusion if confirmed (box visible in image).
• Remove raw OCR dump ('On-image text' block) from consumer-facing description.
• Remove draft/heuristic metadata fields (draft_source, needs_human_review) from any published-facing output.
• Add bilingual labelling compliance note for Canadian market (CASL / Competition Act / Consumer Packaging and Labelling Act may require French on packaging).
• Confirm country of origin for customs and marketplace compliance; image context suggests China — populate param_country_of_origin once verified.

## Suggested fixes (FR)

• Supprimer tous les textes de brouillon internes avant publication : retirer le bloc 'Ébauche construite à partir de la vision...', 'À réviser avant publication', le bloc 'Texte sur l image (latin/chiffres seulement...)', et la ligne '*(Confiance vision : medium_ocr_fallback)*'.
• Corriger param_power : remplacer '1900Watts' par '1900 W' (norme SI, convention canadienne-française).
• Traduire la ligne d'accessoires dans le corps de la description : remplacer '2 styling nozzles included' par '2 buses de concentration incluses'.
• Mettre à jour param_colour de 'Non précisé' à 'Noir' (couleur clairement visible sur l'image).
• Ajouter une note sur la certification dans param_certifications_visible : 'CCC (visible sur l image) — certification canadienne (CSA/cUL) non confirmée'.
• Envisager d'ajouter un avertissement de conformité canadienne : les appareils électriques vendus au Canada doivent porter une marque de certification reconnue par le gouvernement (CSA, cUL, etc.) — à vérifier auprès du fournisseur avant mise en vente.
• Retirer source_brouillon et revision_requise des attributs publiés (champs internes uniquement).
• Uniformiser la langue du titre : 'Sèche-cheveux professionnel Kangfu 8881 — 1900 W' serait plus informatif pour l'acheteur.

