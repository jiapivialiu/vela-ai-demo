# Copy quality review

**Status**: `revise`
**Summary**: EN: English copy reviewer call failed. | FR: The French listing is a draft placeholder with mixed-language content and incomplete localization; core facts (brand, model, wattage, 2 nozzles) are grounded but the description is unpublishable as-is.

## Scores
- **grounding**: 0.5
- **factual_tone**: 0.5

## Exaggeration / hype findings

- No exaggerated claims detected; the listing avoids superlatives and unsupported performance claims.

## Conflicts with structured attributes

- param_power is listed as '1900Watts' (no space); image and attributes both confirm '1900W' — formatting should be normalized to '1900 W'.
- param_included_accessories says '2 buses de concentration' but the structured attribute says '2 styling nozzles included' — translation is acceptable but should be verified against image (image shows 2 concentrator nozzles, consistent).
- Title 'Sèche-cheveux — Électroménager de soins personnels' omits brand and model, which are confirmed by image; this creates a conflict with key_attributes that do include them.
- source_brouillon and revision_requise are internal metadata fields exposed in the public-facing key_attributes — these should be removed before publication.

## Image / visual mismatches

- Image clearly shows the KANGFU logo and model '8881' on the dryer body — the title does not mention brand or model, which is a missed grounding opportunity rather than a mismatch.
- Image shows a CCC certification mark on the dryer body; param_certifications_visible is listed as 'Non précisé' — this is a factual omission (though CCC is a Chinese market cert and may not be relevant for Canadian listing, it should at minimum be noted or explicitly excluded).
- Image shows the dryer is black; param_colour is 'Non précisé' — colour is clearly visible and should be populated as 'Noir'.
- Image shows a retail box with KANGFU branding; no Canadian safety certification (CSA, cUL, or cETL) is visible — param_certifications_visible should flag this absence explicitly for compliance review.

## Suggested fixes (EN)

_(none)_

## Suggested fixes (FR)

• Mettre à jour le titre pour inclure la marque et le modèle, ex. : 'Sèche-cheveux professionnel KANGFU 8881 — 1900 W'.
• Remplacer le corps de description par un texte rédigé en français canadien correct, sans balises de brouillon ni avertissements internes.
• Corriger param_power : '1900Watts' → '1900 W'.
• Renseigner param_colour : 'Noir' (clairement visible sur l'image).
• Supprimer les champs internes source_brouillon et revision_requise de key_attributes avant publication.
• Ajouter une note sur param_certifications_visible : indiquer 'Marque CCC visible (marché chinois) — certification canadienne (CSA/cUL) non confirmée' pour déclencher une vérification de conformité.
• Retirer les lignes de texte OCR brut ('8881, KANGFU, 1900, Watts, FU') de la description publique — ces données appartiennent aux métadonnées internes uniquement.
• Vérifier et compléter param_dimensions et param_weight à partir de la fiche technique du fabricant avant publication.
• S'assurer que '2 buses de concentration' correspond bien aux accessoires physiques inclus dans l'emballage canadien.

