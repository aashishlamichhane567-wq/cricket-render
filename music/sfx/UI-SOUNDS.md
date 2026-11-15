# Pointing sounds

From the earshot bank (github.com/Leonxlnx/earshot, MIT; the bank is synthesised in code, no recordings):

| file | earshot id | used for | level |
|---|---|---|---|
| ui-ring.wav | pro_notify/ding_2045 | a circle drawing on | peak = the voice's peaks (`pk: 0`) |
| ui-pop.wav | pro_pop_in/round | a label appearing | peak = the voice's peaks (`pk: 0`) |
| ui-arrow.wav | pro_notify/ding_2045 (same as ui-ring) | an arrow drawing on | peak = the voice's peaks (`pk: 0`) |

Chosen 2026-11-13 with earshot against the owner's reference Short P4e7XbHtux4 (Textuju, 1.16M views), whose pops and dings
stand a median 14.2 dB over their bed. Each candidate was mixed into 028's audio at its 11 pointing moments and measured
(median prominence): ding_2045 10.8, ding_1930 10.3, pop/2 8.5, celesta 7.6, drip/plink 7.4, pro_pop_in/round 7.0, glock 5.9, bloop 5.7.
Soft, rounded sounds are masked by our dense narration; bright, sharp ones cut through.
Re-render with: earshot sfx render <id> -o <file>

2026-11-14 re-match to the reference: earshot labels its sounds 'chat message pop' (8x, on text) and 'glockenspiel note' (4x, on the circle AND the arrows).
earshot compare vs its pops: pro_pop_in/round 0.41, sfxmint ready-pop-01 0.31, pop/2 0.29. Vs its chimes: ding_2045 0.22 best; synth glock 0.07-0.12 despite the label.
So circle and arrow share one chime, as the reference does. sfxmint WAV now needs a paid pack; MP3 is free but 2 downloads/day keyless.

2026-11-14 audit: ui-pop is pop/2 again. pro_pop_in/round (028) has 99% of its energy below 300 Hz, so phones cannot play it;
it was picked by `earshot compare`, which is not a reliable match (knowledge/voice.md F.1). ui-arrow = the ui-ring ding.
A riser + sub hit were added and removed the same day: they came from misread footymemology labels.
