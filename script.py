
import requests
import re
import json
import html
import time
from lxml import etree
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
import subprocess
import os

# =========================================
# 🔹 CARGAR DATA.JS DESDE EL NAVEGADOR
# =========================================

def get_ajax_data_directly(driver):
    """Busca la variable ajaxData ignorando copias viejas en memoria."""
    # Intentar primero en el contexto principal
    try:
        data = driver.execute_script("return (typeof ajaxData !== 'undefined') ? JSON.stringify(ajaxData) : null;")
        if data: return json.loads(data), None
    except: pass
        
    # Buscar en iframes, priorizando el que esté VISIBLE y tenga data
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            try:
                if not iframe.is_displayed(): continue
                
                driver.switch_to.frame(iframe)
                data = driver.execute_script("return (typeof ajaxData !== 'undefined') ? JSON.stringify(ajaxData) : null;")
                if data:
                    res = json.loads(data)
                    driver.switch_to.default_content()
                    return res, iframe
                driver.switch_to.default_content()
            except:
                try: driver.switch_to.default_content()
                except: pass
    except: pass
            
    return None, None


# =========================================
# 🔹 FUNCIONES ORIGINALES DE PARSEO (LIMPIAR XML Y EXTRAER)
# =========================================

def clean_xml(xml_string):
    xml_string = html.unescape(xml_string)
    xml_string = html.unescape(xml_string)
    xml_string = re.sub(r'<!\[CDATA\[.*?\]\]>', '', xml_string, flags=re.DOTALL)
    xml_string = re.sub(r'\s+data-author="\{.*?\}"', '', xml_string)
    xml_string = re.sub(r'\s+data-author=(?:"[^"]*"|\'[^\']*\')', '', xml_string)
    xml_string = re.sub(r'<assets>.*?</assets>', '', xml_string, flags=re.DOTALL)
    xml_string = re.sub(r'=\s*""+"', '=""', xml_string)
    xml_string = re.sub(r'label="<p>(.*?)</p>"', r'label="\1"', xml_string)
    xml_string = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;', xml_string)
    return xml_string

def get_all_text(node):
    parts = []
    if node.text:
        parts.append(node.text.strip())
    for child in node:
        parts.append(get_all_text(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)

def unique_preserve_order(values):
    vistos = set()
    resultado = []
    for value in values:
        if value in vistos:
            continue
        vistos.add(value)
        resultado.append(value)
    return resultado

def parse_question(xml_string, nombre, tipo):
    xml_string = clean_xml(xml_string)
    try:
        root = etree.fromstring(xml_string.encode('utf-8'))
    except Exception as e:
        return None

    ns_url = root.nsmap.get(None, "http://www.imsglobal.org/xsd/imsqti_v2p1")
    ns = {"qti": ns_url}

    instruccion = ""
    for p in root.findall(".//qti:div[@id='rubric']//qti:p", ns):
        txt = get_all_text(p)
        if txt:
            instruccion = txt
            break

    inline_blocks = root.findall(".//qti:inlineChoiceInteraction", ns)
    if inline_blocks:
        preguntas_grupo = []
        seen_inline_responses = set()
        # Buscamos cada interaccion donde sea que este (sin forzar <p>)
        for interaction in inline_blocks:
            resp_id = interaction.attrib.get("responseIdentifier", "")
            if resp_id and resp_id in seen_inline_responses:
                continue
            if resp_id:
                seen_inline_responses.add(resp_id)
            correct_id = None
            correct_decl = root.find(
                f".//qti:responseDeclaration[@identifier='{resp_id}']//qti:correctResponse//qti:value",
                ns,
            )
            if correct_decl is not None: correct_id = correct_decl.text
            
            opciones, correcta = [], None
            for choice in interaction.findall(".//qti:inlineChoice", ns):
                texto = get_all_text(choice).strip()
                ident = choice.attrib.get("identifier", "")
                opciones.append(texto)
                if ident == correct_id: correcta = texto
                
            if correcta:
                preguntas_grupo.append({"pregunta": instruccion, "opciones": opciones, "correctas": [correcta]})
        
        if len(preguntas_grupo) == 1:
            return {"archivo": nombre, "tipo": tipo, "pregunta": preguntas_grupo[0]["pregunta"], "opciones": preguntas_grupo[0]["opciones"], "correctas": preguntas_grupo[0]["correctas"]}
        elif len(preguntas_grupo) > 1:
            return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion, "sub_preguntas": preguntas_grupo, "opciones": [], "correctas": []}

    choice_interactions = root.findall(".//qti:choiceInteraction", ns)
    if choice_interactions:
        textos = []
        for p in root.findall(".//qti:div[@id='contentblock']//qti:p", ns):
            txt = get_all_text(p)
            if txt:
                textos.append(txt)
        pregunta_txt = instruccion + " " + " ".join(textos) if instruccion else " ".join(textos)

        preguntas_grupo = []
        seen_choice_responses = set()
        for idx, interaction in enumerate(choice_interactions):
            resp_id = interaction.attrib.get("responseIdentifier", "")
            if resp_id and resp_id in seen_choice_responses:
                continue
            if resp_id:
                seen_choice_responses.add(resp_id)
            opciones, mapa = [], {}
            for choice in interaction.findall("qti:simpleChoice", ns):
                texto = get_all_text(choice).strip()
                ident = choice.attrib.get("identifier", "")
                if texto:
                    opciones.append(texto)
                mapa[ident] = texto

            if resp_id:
                correct_nodes = root.findall(
                    f".//qti:responseDeclaration[@identifier='{resp_id}']//qti:correctResponse//qti:value",
                    ns,
                )
            elif len(choice_interactions) == 1:
                correct_nodes = root.findall(".//qti:correctResponse//qti:value", ns)
            else:
                correct_nodes = []

            correctas = []
            for val in correct_nodes:
                cid = (val.text or "").strip()
                if not cid:
                    continue
                partes = cid.split() if " " in cid else [cid]
                for parte in partes:
                    texto = mapa.get(parte, "").strip()
                    if texto and texto not in correctas:
                        correctas.append(texto)

            if correctas:
                preguntas_grupo.append(
                    {
                        "pregunta": pregunta_txt.strip() or instruccion.strip() or f"choiceInteraction_{idx + 1}",
                        "opciones": opciones,
                        "correctas": correctas,
                    }
                )

        if len(preguntas_grupo) == 1:
            return {
                "archivo": nombre,
                "tipo": tipo,
                "pregunta": preguntas_grupo[0]["pregunta"],
                "opciones": preguntas_grupo[0]["opciones"],
                "correctas": preguntas_grupo[0]["correctas"],
            }
        elif len(preguntas_grupo) > 1:
            return {
                "archivo": nombre,
                "tipo": tipo,
                "pregunta": pregunta_txt.strip(),
                "sub_preguntas": preguntas_grupo,
                "opciones": [],
                "correctas": [],
            }

    gap_interactions = root.findall(".//qti:gapMatchInteraction", ns)
    if gap_interactions:
        palabras_orden = []
        seen_gap_responses = set()
        for interaction in gap_interactions:
            resp_id = interaction.attrib.get("responseIdentifier", "")
            if resp_id and resp_id in seen_gap_responses:
                continue
            if resp_id:
                seen_gap_responses.add(resp_id)

            mapa_gap = {}
            for gap_text in interaction.findall("qti:gapText", ns):
                ident = gap_text.attrib.get("identifier", "")
                if ident:
                    mapa_gap[ident] = get_all_text(gap_text).strip()

            orden_gaps = []
            for gap in interaction.findall(".//qti:gap", ns):
                gap_id = gap.attrib.get("identifier") or gap.attrib.get("id") or ""
                if gap_id and gap_id not in orden_gaps:
                    orden_gaps.append(gap_id)

            if resp_id:
                correct_nodes = root.findall(
                    f".//qti:responseDeclaration[@identifier='{resp_id}']//qti:correctResponse//qti:value",
                    ns,
                )
            else:
                correct_nodes = root.findall(".//qti:correctResponse//qti:value", ns)

            respuestas_por_gap = {}
            for val in correct_nodes:
                par = (val.text or "").strip()
                partes = par.split()
                if len(partes) != 2:
                    continue

                gap_text_id, target_gap_id = partes
                texto = mapa_gap.get(gap_text_id, "").strip()
                if not texto:
                    continue

                respuestas_por_gap.setdefault(target_gap_id, []).append(texto)

            for gap_id in orden_gaps:
                opciones_gap = unique_preserve_order(respuestas_por_gap.get(gap_id, []))
                if opciones_gap:
                    # Algunos XML listan varias parejas validas para el mismo gap
                    # cuando una misma palabra puede reutilizarse. Aqui queremos
                    # una respuesta final por hueco, no todas las combinaciones.
                    palabras_orden.append(opciones_gap[0])

        return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion.strip(), "opciones": palabras_orden, "correctas": palabras_orden}

    text_entry_interactions = root.findall(".//qti:textEntryInteraction", ns)
    if text_entry_interactions:
        correctas_dict = {}
        for decl in root.findall(".//qti:responseDeclaration", ns):
            ident = decl.attrib.get("identifier", "")
            val_node = decl.find(".//qti:correctResponse//qti:value", ns)
            if val_node is not None: correctas_dict[ident] = (val_node.text or "").strip()
        seen_text_entries = set()
        correctas = []
        for interaction in text_entry_interactions:
            resp_id = interaction.attrib.get("responseIdentifier", "")
            if not resp_id or resp_id in seen_text_entries or resp_id not in correctas_dict:
                continue
            seen_text_entries.add(resp_id)
            correctas.append(correctas_dict[resp_id])
        return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion.strip(), "opciones": [], "correctas": correctas}

    opciones, mapa = [], {}
    for choice in root.xpath(".//*[local-name()='simpleChoice' or local-name()='simpleAssociableChoice']"):
        texto = get_all_text(choice)
        ident = choice.attrib.get("identifier", "")
        if texto: opciones.append(texto)
        mapa[ident] = texto
    correctas = []
    for val in root.findall(".//qti:correctResponse//qti:value", ns):
        cid = (val.text or "").strip()
        if not cid: continue
        if " " in cid:
            partes = [mapa.get(p, p).strip() for p in cid.split() if mapa.get(p)]
            if partes: correctas.append(" -> ".join(partes))
        elif cid in mapa:
            correctas.append(mapa[cid])
    return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion.strip(), "opciones": opciones, "correctas": correctas}

def parse_learning_object(xml_string):
    xml_string = clean_xml(xml_string)
    try:
        root = etree.fromstring(xml_string.encode('utf-8'))
    except:
        return []
    screens = []
    for screen in root.findall(".//screen"):
        screens.append({
            "archivo": screen.findtext("name"), 
            "tipo": screen.findtext("activityTypeName"),
            "puntuacion": screen.findtext("maximumscore")
        })
    return screens

# =========================================
# 🔹 RESOLUCIÓN ULTRA-RÁPIDA (DETERMINISTA)
# =========================================

def resolver_pantalla_js(driver, frame_elemento, respuestas_planas):
    """
    Inyecta un código Javascript que lee la lista de respuestas planas y, 
    de manera automática, hace click en los botones en pantalla equivalentes, 
    o rellena los inputs que encuentre vacíos. ¡Tiempo de ejecución 0 milisegundos!
    """
    if not respuestas_planas:
        return False
        
    js_code = r"""
    let answers = arguments[0];
    let callback = arguments[1];
    
    function supremeClick(el) {
        if(!el) return;
        el.scrollIntoView({behavior: "auto", block: "center"});
        ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
            el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
        });
    }

    function normalizeText(text) {
        return (text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    }

    function isVisible(el) {
        return !!el && el.offsetParent !== null;
    }

    function isReallyVisible(el) {
        if (!isVisible(el)) return false;
        let rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function dedupeElements(elements) {
        let out = [];
        let seen = new Set();
        for (let el of elements) {
            if (!el || seen.has(el)) continue;
            seen.add(el);
            out.push(el);
        }
        return out;
    }

    function sortByDocumentOrder(elements) {
        return elements.slice().sort((a, b) => {
            if (a === b) return 0;
            let pos = a.compareDocumentPosition(b);
            if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
            if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
            let ar = a.getBoundingClientRect();
            let br = b.getBoundingClientRect();
            if (ar.top !== br.top) return ar.top - br.top;
            return ar.left - br.left;
        });
    }

    function getDropdownRoot(el) {
        if (!el) return null;

        let prioritySelectors = [
            '[role="combobox"]',
            'button[aria-haspopup]',
            '[aria-haspopup="listbox"]',
            '.listbox'
        ];

        for (let sel of prioritySelectors) {
            if (el.matches && el.matches(sel)) return el;
            let parent = el.closest && el.closest(sel);
            if (parent) return parent;
        }

        let fallbackSelectors = [
            '.drop-label.listbox__label',
            '.listbox__label'
        ];

        for (let sel of fallbackSelectors) {
            if (el.matches && el.matches(sel)) return el;
            let parent = el.closest && el.closest(sel);
            if (parent) return parent;
        }

        return el;
    }

    function getDropdownClickTargets(drop) {
        if (!drop) return [];
        return dedupeElements([
            drop,
            drop.querySelector && drop.querySelector('.drop-label.listbox__label'),
            drop.querySelector && drop.querySelector('.listbox__label'),
            drop.querySelector && drop.querySelector('[role="combobox"]'),
            drop.querySelector && drop.querySelector('button'),
            drop.querySelector && drop.querySelector('[role="button"]'),
            drop.parentElement
        ].filter(Boolean)).filter(isReallyVisible);
    }

    function getDropdownDisplayText(drop) {
        if (!drop) return '';

        let labelNode = drop.matches && (
            drop.matches('.drop-label.listbox__label')
            || drop.matches('.listbox__label')
            || drop.matches('[role="combobox"]')
            || drop.matches('button')
        ) ? drop : (
            drop.querySelector && (
                drop.querySelector('.drop-label.listbox__label')
                || drop.querySelector('.listbox__label')
                || drop.querySelector('[role="combobox"]')
                || drop.querySelector('button')
            )
        );

        let text = normalizeText((labelNode && (labelNode.innerText || labelNode.textContent)) || '');
        if (text) return text;

        return normalizeText(drop.innerText || drop.textContent || '');
    }

    function getVisibleDropdownOptions(drop) {
        let optionSelectors = 'li.listbox__choice, [role="option"], .option-view, button[role="option"]';
        let containers = [];
        let targets = getDropdownClickTargets(drop);

        for (let target of targets) {
            if (!target || !target.getAttribute) continue;

            let ids = [
                target.getAttribute('aria-controls'),
                target.getAttribute('aria-owns'),
                target.id ? target.id + '-listbox' : null
            ].filter(Boolean);

            for (let id of ids) {
                let node = document.getElementById(id);
                if (node) containers.push(node);
            }
        }

        containers = dedupeElements(containers);

        let options = [];
        for (let container of containers) {
            options.push(...Array.from(container.querySelectorAll(optionSelectors)));
        }

        if (options.length === 0) {
            options = Array.from(document.querySelectorAll(optionSelectors));
        }

        return dedupeElements(options).filter(el => {
            if (!isReallyVisible(el)) return false;
            return normalizeText(el.innerText || el.textContent || '').length > 0;
        });
    }

    async function openDropdown(drop) {
        let targets = getDropdownClickTargets(drop);
        for (let target of targets) {
            try {
                supremeClick(target);
                if (typeof target.click === 'function') target.click();
            } catch (e) {}

            await new Promise(r => setTimeout(r, 120));
            if (getVisibleDropdownOptions(drop).length > 0) {
                return true;
            }
        }

        return getVisibleDropdownOptions(drop).length > 0;
    }

    async function selectDropdownAnswer(drop, answerText) {
        let ansLow = normalizeText(answerText);
        if (!drop || !ansLow) return false;

        let currentText = getDropdownDisplayText(drop);
        if (currentText === ansLow || currentText.includes(ansLow)) {
            return true;
        }

        let opened = await openDropdown(drop);
        if (!opened) return false;

        let options = getVisibleDropdownOptions(drop);
        options.sort((a, b) => {
            let aText = normalizeText(a.innerText || a.textContent || '');
            let bText = normalizeText(b.innerText || b.textContent || '');
            let aExact = aText === ansLow ? 0 : 1;
            let bExact = bText === ansLow ? 0 : 1;
            if (aExact !== bExact) return aExact - bExact;
            return aText.length - bText.length;
        });

        let option = options.find(el => {
            let text = normalizeText(el.innerText || el.textContent || '');
            return text === ansLow || text.includes(ansLow) || ansLow.includes(text);
        });

        if (!option) {
            supremeClick(document.body);
            return false;
        }

        try {
            supremeClick(option);
            if (typeof option.click === 'function') option.click();
        } catch (e) {}

        await new Promise(r => setTimeout(r, 160));
        currentText = getDropdownDisplayText(drop);
        if (currentText === ansLow || currentText.includes(ansLow)) {
            return true;
        }

        await new Promise(r => setTimeout(r, 160));
        currentText = getDropdownDisplayText(drop);
        if (currentText === ansLow || currentText.includes(ansLow)) {
            return true;
        }

        supremeClick(document.body);
        return false;
    }

    function getClickableAncestor(el) {
        if (!el) return null;
        if (el.matches && el.matches('button, [role="button"], a, label, li')) return el;
        return el.closest ? el.closest('button, [role="button"], a, label, li') : null;
    }

    function getVisibleWordBankItems(answerText) {
        let ansLow = normalizeText(answerText);
        if (!ansLow) return [];

        let items = [];
        let seen = new Set();
        let selectors = [
            '.pool.ui-droppable .drag_element.gap_match_drag',
            '.pool-wrapper .drag_element.gap_match_drag',
            '.gap_match_gap_text_view.has_drag .drag_element.gap_match_drag'
        ];

        for (let selector of selectors) {
            for (let el of Array.from(document.querySelectorAll(selector))) {
                if (!isReallyVisible(el)) continue;

                let text = normalizeText(el.innerText || el.textContent || '');
                if (!text) continue;
                if (!(text === ansLow || text.includes(ansLow) || ansLow.includes(text))) continue;

                let pool = el.closest('.pool.ui-droppable, .pool-wrapper');
                if (!pool || !isReallyVisible(pool)) continue;

                let modelId = el.getAttribute('data-model-id') || '';
                let uniqueKey = modelId || ((el.querySelector('button') && el.querySelector('button').getAttribute('aria-labelledby')) || text);
                if (seen.has(uniqueKey)) continue;
                seen.add(uniqueKey);

                let rect = el.getBoundingClientRect();
                items.push({
                    node: el,
                    button: el.querySelector('button'),
                    modelId: modelId,
                    text: text,
                    top: rect.top,
                    left: rect.left,
                    exact: text === ansLow
                });
            }
        }

        items.sort((a, b) => {
            if (a.exact !== b.exact) return a.exact ? -1 : 1;
            if (a.top !== b.top) return b.top - a.top;
            return a.left - b.left;
        });

        return items;
    }

    function didSpecificWordBankPlacementChange(item, beforeTop) {
        if (!item) return false;

        let current = null;
        if (item.modelId) {
            current = Array.from(document.querySelectorAll('.drag_element.gap_match_drag')).find(el => {
                return el.getAttribute('data-model-id') === item.modelId && isReallyVisible(el);
            }) || null;
        }

        if (!current) {
            let remaining = getVisibleWordBankItems(item.text);
            return remaining.length === 0;
        }

        let pool = current.closest('.pool.ui-droppable, .pool-wrapper');
        if (!pool || !isReallyVisible(pool)) return true;

        let rect = current.getBoundingClientRect();
        return rect.top < beforeTop - 100;
    }

    function getFilledWordBankTargets() {
        let selectors = [
            '.gap_match_gap_text_view.has_drag',
            '.gap_match_gap_view.has_drag',
            '.gap.has_drag',
            '[class*="gap_match_gap"].has_drag'
        ];

        let nodes = [];
        for (let selector of selectors) {
            nodes.push(...Array.from(document.querySelectorAll(selector)));
        }

        return dedupeElements(nodes)
            .filter(isReallyVisible)
            .map(node => node.querySelector('.drag_element.gap_match_drag, .drag_element, .gap_match_drag') || node);
    }

    function getWordBankPlacementSnapshot(answerText) {
        let ansLow = normalizeText(answerText);
        let filledTargets = getFilledWordBankTargets();
        let matchingCount = 0;

        for (let node of filledTargets) {
            let text = normalizeText(node.innerText || node.textContent || '');
            if (!ansLow || !text) continue;
            if (text === ansLow || text.includes(ansLow) || ansLow.includes(text)) {
                matchingCount++;
            }
        }

        return {
            filledCount: filledTargets.length,
            matchingCount: matchingCount
        };
    }

    function didWordBankSnapshotAdvance(beforeSnapshot, answerText) {
        let afterSnapshot = getWordBankPlacementSnapshot(answerText);
        return afterSnapshot.filledCount > beforeSnapshot.filledCount
            || afterSnapshot.matchingCount > beforeSnapshot.matchingCount;
    }

    async function clickSpecificWordBankAnswer(answerText) {
        let items = getVisibleWordBankItems(answerText);
        if (items.length === 0) return false;

        for (let item of items) {
            let clickTargets = dedupeElements([item.button, item.node].filter(Boolean));
            for (let target of clickTargets) {
                let beforeSnapshot = getWordBankPlacementSnapshot(answerText);
                try {
                    supremeClick(target);
                    if (typeof target.click === 'function') target.click();
                } catch (e) {}

                await new Promise(r => setTimeout(r, 180));
                if (didSpecificWordBankPlacementChange(item, item.top) || didWordBankSnapshotAdvance(beforeSnapshot, answerText)) {
                    return true;
                }
            }
        }

        return false;
    }

    function collectWordBankCandidates(answerText) {
        let ansLow = normalizeText(answerText);
        if (!ansLow) return [];

        let candidates = [];
        let seen = new Set();

        for (let el of Array.from(document.querySelectorAll('button, [role="button"], a, li, span, div'))) {
            if (!isReallyVisible(el)) continue;

            let rawText = normalizeText(el.innerText || el.textContent || '');
            if (!rawText) continue;
            if (!(rawText === ansLow || rawText.includes(ansLow) || ansLow.includes(rawText))) continue;

            let clickTarget = getClickableAncestor(el) || el;
            if (!isReallyVisible(clickTarget)) continue;

            let rect = clickTarget.getBoundingClientRect();
            if (rect.width > 260 || rect.height > 120 || rect.width < 20 || rect.height < 18) continue;

            if (seen.has(clickTarget)) continue;
            seen.add(clickTarget);

            let targetText = normalizeText(clickTarget.innerText || clickTarget.textContent || '');
            let exact = rawText === ansLow || targetText === ansLow;
            let buttonLike = clickTarget.matches && clickTarget.matches('button, [role="button"], a, label, li');

            candidates.push({
                node: el,
                clickTarget: clickTarget,
                text: targetText || rawText,
                rect: rect,
                exact: exact,
                buttonLike: buttonLike
            });
        }

        candidates.sort((a, b) => {
            if (a.exact !== b.exact) return a.exact ? -1 : 1;
            if (a.buttonLike !== b.buttonLike) return a.buttonLike ? -1 : 1;
            if (a.rect.top !== b.rect.top) return b.rect.top - a.rect.top;
            if (a.rect.width !== b.rect.width) return a.rect.width - b.rect.width;
            return a.rect.left - b.rect.left;
        });

        return candidates;
    }

    function didWordBankPlacementChange(answerText, beforeTop) {
        let after = collectWordBankCandidates(answerText);
        if (after.length === 0) return true;

        let stillInBank = after.some(c => c.rect.top >= beforeTop - 20);
        if (!stillInBank) return true;

        let highestTop = Math.max(...after.map(c => c.rect.top));
        return highestTop < beforeTop - 40;
    }

    async function clickWordBankAnswer(answerText) {
        let exactCambridgeResult = await clickSpecificWordBankAnswer(answerText);
        if (exactCambridgeResult) return true;

        let candidates = collectWordBankCandidates(answerText);
        if (candidates.length === 0) return false;

        let beforeTop = candidates[0].rect.top;
        let targets = dedupeElements(
            candidates.slice(0, 3).flatMap(c => [c.node, c.clickTarget].filter(Boolean))
        );

        for (let target of targets) {
            let beforeSnapshot = getWordBankPlacementSnapshot(answerText);
            try {
                supremeClick(target);
                if (typeof target.click === 'function') target.click();
            } catch (e) {}

            await new Promise(r => setTimeout(r, 180));
            if (didWordBankPlacementChange(answerText, beforeTop) || didWordBankSnapshotAdvance(beforeSnapshot, answerText)) {
                return true;
            }
        }

        return false;
    }

    function getAssociatedInput(el) {
        if (!el) return null;
        if (el.matches && el.matches('input[type="radio"], input[type="checkbox"]')) return el;

        let nestedInput = el.querySelector && el.querySelector('input[type="radio"], input[type="checkbox"]');
        if (nestedInput) return nestedInput;

        if (el.getAttribute) {
            let forId = el.getAttribute('for');
            if (forId) {
                let linked = document.getElementById(forId);
                if (linked && linked.matches && linked.matches('input[type="radio"], input[type="checkbox"]')) {
                    return linked;
                }
            }
        }

        return null;
    }

    function getChoiceRoleNode(el) {
        if (!el) return null;
        if (el.matches && el.matches('[role="radio"], [role="checkbox"]')) return el;
        return el.closest ? el.closest('[role="radio"], [role="checkbox"]') : null;
    }

    function getChoiceType(el) {
        let input = getAssociatedInput(el);
        if (input && input.type) return input.type.toLowerCase();

        let roleNode = getChoiceRoleNode(el);
        if (roleNode) {
            let role = (roleNode.getAttribute('role') || '').toLowerCase();
            if (role === 'checkbox' || role === 'radio') return role;
        }

        return 'radio';
    }

    function getChoiceText(el) {
        if (!el) return '';

        let text = normalizeText(el.innerText || el.textContent || '');
        if (text) return text;

        let aria = normalizeText((el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'))) || '');
        if (aria) return aria;

        let input = getAssociatedInput(el);
        if (input) {
            let label = (input.labels && input.labels[0]) || (input.closest && input.closest('label'));
            if (label && label !== el) {
                let labelText = normalizeText(label.innerText || label.textContent || '');
                if (labelText) return labelText;
            }
        }

        return '';
    }

    function isChoiceSelected(el) {
        if (!el) return false;

        let input = getAssociatedInput(el);
        if (input) return !!input.checked;

        let roleNode = getChoiceRoleNode(el) || el;
        return roleNode.getAttribute('aria-checked') === 'true'
            || roleNode.classList.contains('selected')
            || roleNode.classList.contains('active')
            || roleNode.classList.contains('checked')
            || roleNode.classList.contains('is-selected');
    }

    function getChoiceGroupKey(el) {
        if (!el) return 'choice-group-root';

        let input = getAssociatedInput(el);
        if (input) {
            let inputType = (input.type || '').toLowerCase();
            if (inputType === 'radio' && input.name) {
                return 'radio-name:' + input.name;
            }
        }

        let roleNode = getChoiceRoleNode(el);
        if (roleNode) {
            let roleGroupName = roleNode.getAttribute('name')
                || roleNode.getAttribute('data-name')
                || roleNode.getAttribute('aria-labelledby');
            if (roleGroupName) {
                return 'role-group:' + roleGroupName;
            }
        }

        let selectors = [
            'fieldset',
            '[role="radiogroup"]',
            '[role="group"]',
            '.question',
            '.question-container',
            '.interaction',
            '.interaction-container',
            '.answers',
            '.options',
            'ul',
            'ol',
            'table',
            'tbody',
            'tr'
        ];

        for (let sel of selectors) {
            let group = el.closest && el.closest(sel);
            if (!group) continue;

            let count = Array.from(
                group.querySelectorAll('label, [role="radio"], [role="checkbox"], .option, .choice, input[type="radio"], input[type="checkbox"]')
            ).filter(isVisible).length;

            if (count >= 2 && count <= 20) {
                if (!group.dataset.choiceGroupKey) {
                    group.dataset.choiceGroupKey = 'choice-group-' + Math.random().toString(36).slice(2, 10);
                }
                return group.dataset.choiceGroupKey;
            }
        }

        let parent = el.parentElement;
        if (!parent) return 'choice-group-root';

        if (!parent.dataset.choiceGroupKey) {
            parent.dataset.choiceGroupKey = 'choice-group-' + Math.random().toString(36).slice(2, 10);
        }
        return parent.dataset.choiceGroupKey;
    }

    function clickChoice(option) {
        if (!option || !option.node) return false;

        let targets = [];
        let seen = new Set();
        function addTarget(el) {
            if (!el || seen.has(el)) return;
            seen.add(el);
            targets.push(el);
        }

        addTarget(option.node);
        addTarget(option.roleNode);
        addTarget(option.input);

        if (option.input) {
            let label = (option.input.labels && option.input.labels[0]) || (option.input.closest && option.input.closest('label'));
            addTarget(label);
        }

        for (let target of targets) {
            try {
                if (typeof target.focus === 'function') target.focus();
            } catch (e) {}

            try {
                if (typeof target.click === 'function') target.click();
            } catch (e) {}

            if (isChoiceSelected(target) || isChoiceSelected(option.node)) {
                return true;
            }
        }

        for (let target of targets) {
            try {
                if (typeof target.focus === 'function') target.focus();
            } catch (e) {}

            try {
                supremeClick(target);
            } catch (e) {}

            if (isChoiceSelected(target) || isChoiceSelected(option.node)) {
                return true;
            }
        }

        return isChoiceSelected(option.node);
    }

    function parseCategorizedAnswer(answerText) {
        if (!answerText || typeof answerText !== 'string') return null;
        let idx = answerText.indexOf('->');
        if (idx === -1) return null;

        let category = answerText.slice(0, idx).trim();
        let answer = answerText.slice(idx + 2).trim();
        if (!category || !answer) return null;

        return { category, answer };
    }

    function normalizeCategoryLabel(label) {
        let text = normalizeText(label);
        if (!text) return '';

        text = text.replace(/^category\s+/, '');
        text = text.replace(/\.\s*place your answer in this category$/, '');
        text = text.replace(/\s*place your answer in this category$/, '');
        return text.trim();
    }

    function getJQueryMvcView(node) {
        if (!node) return null;
        let jq = window.jQuery || window.$;
        if (!jq) return null;

        try {
            return jq(node).data('mvcView') || null;
        } catch (e) {
            return null;
        }
    }

    function getSortingSnapshot() {
        let categories = Array.from(document.querySelectorAll('.drop_zone.osSorting__category'))
            .filter(isReallyVisible)
            .map(node => ({
                node: node,
                label: normalizeCategoryLabel(
                    node.getAttribute('aria-label')
                    || (node.querySelector('.category_title') && (node.querySelector('.category_title').innerText || node.querySelector('.category_title').textContent))
                    || ''
                ),
                texts: Array.from(node.querySelectorAll('.draggable.drag_item'))
                    .filter(isReallyVisible)
                    .map(el => normalizeText(el.innerText || el.textContent || ''))
                    .filter(Boolean),
                emptySlots: Array.from(node.querySelectorAll('.drop_item_zone.droppable'))
                    .filter(slot => !slot.classList.contains('has_drag_item'))
            }));

        let poolItems = Array.from(document.querySelectorAll('.dds_wordpool_view .draggable.drag_item'))
            .filter(isReallyVisible)
            .map(el => normalizeText(el.innerText || el.textContent || ''))
            .filter(Boolean);

        return {
            categories: categories,
            poolItems: poolItems,
            filledCount: categories.reduce((sum, cat) => sum + cat.texts.length, 0)
        };
    }

    function getSortingPoolItemCandidates(answerText) {
        let ansLow = normalizeText(answerText);
        if (!ansLow) return [];

        let items = Array.from(document.querySelectorAll('.dds_wordpool_view .draggable.drag_item'))
            .filter(isReallyVisible)
            .map(node => {
                let text = normalizeText(node.innerText || node.textContent || '');
                let button = node.querySelector('button');
                return {
                    node: node,
                    button: button,
                    text: text,
                    exact: text === ansLow,
                    view: getJQueryMvcView(node)
                };
            })
            .filter(item => item.text && (item.text === ansLow || item.text.includes(ansLow) || ansLow.includes(item.text)));

        items.sort((a, b) => {
            if (a.exact !== b.exact) return a.exact ? -1 : 1;
            return a.text.length - b.text.length;
        });

        return items;
    }

    function getSortingSourceCandidates(answerText) {
        let ansLow = normalizeText(answerText);
        if (!ansLow) return [];

        let items = Array.from(document.querySelectorAll('.draggable.drag_item'))
            .filter(isReallyVisible)
            .map(node => {
                let text = normalizeText(node.innerText || node.textContent || '');
                let categoryNode = node.closest('.drop_zone.osSorting__category');
                return {
                    node: node,
                    text: text,
                    exact: text === ansLow,
                    inPool: !!node.closest('.dds_wordpool_view'),
                    category: categoryNode ? normalizeCategoryLabel(categoryNode.getAttribute('aria-label') || '') : '',
                    view: getJQueryMvcView(node)
                };
            })
            .filter(item => item.text && (item.text === ansLow || item.text.includes(ansLow) || ansLow.includes(item.text)));

        items.sort((a, b) => {
            if (a.inPool !== b.inPool) return a.inPool ? -1 : 1;
            if (a.exact !== b.exact) return a.exact ? -1 : 1;
            return a.text.length - b.text.length;
        });

        return items;
    }

    function findSortingCategory(categoryText) {
        let targetLow = normalizeCategoryLabel(categoryText);
        if (!targetLow) return null;

        let categories = getSortingSnapshot().categories;
        return categories.find(cat => cat.label === targetLow)
            || categories.find(cat => cat.label.includes(targetLow) || targetLow.includes(cat.label))
            || null;
    }

    function isSortingAnswerInCategory(categoryText, answerText) {
        let targetCategory = findSortingCategory(categoryText);
        if (!targetCategory) return false;

        let ansLow = normalizeText(answerText);
        return targetCategory.texts.some(text => text === ansLow || text.includes(ansLow) || ansLow.includes(text));
    }

    async function clickSortingAnswerInOrder(answerText) {
        let candidates = getSortingPoolItemCandidates(answerText);
        if (candidates.length === 0) return false;

        for (let candidate of candidates) {
            let before = getSortingSnapshot();

            try {
                if (candidate.button && typeof candidate.button.click === 'function') {
                    candidate.button.click();
                } else if (candidate.view && typeof candidate.view.activateTapItem === 'function') {
                    candidate.view.activateTapItem();
                } else if (typeof candidate.node.click === 'function') {
                    candidate.node.click();
                } else {
                    supremeClick(candidate.node);
                }
            } catch (e) {}

            await new Promise(r => setTimeout(r, 140));
            let after = getSortingSnapshot();
            if (after.poolItems.length < before.poolItems.length || after.filledCount > before.filledCount) {
                return true;
            }
        }

        return false;
    }

    async function moveSortingAnswerToCategory(categoryText, answerText) {
        let targetCategory = findSortingCategory(categoryText);
        if (!targetCategory) return false;

        if (isSortingAnswerInCategory(categoryText, answerText)) {
            return true;
        }

        let targetSlot = targetCategory.emptySlots[0] || null;
        if (!targetSlot) return false;

        let targetSlotView = getJQueryMvcView(targetSlot);
        if (!targetSlotView || typeof targetSlotView._doTargetMove !== 'function') {
            return false;
        }

        let candidates = getSortingSourceCandidates(answerText).filter(item => item.category !== targetCategory.label);
        if (candidates.length === 0) return false;

        for (let candidate of candidates) {
            if (!candidate.view || typeof candidate.view.setSelected !== 'function') {
                continue;
            }

            try {
                candidate.view.setSelected();
                targetSlotView._doTargetMove();
            } catch (e) {
                continue;
            }

            await new Promise(r => setTimeout(r, 110));
            if (isSortingAnswerInCategory(categoryText, answerText)) {
                return true;
            }
        }

        return false;
    }

    function buildTextCountMap(values) {
        let map = new Map();
        for (let value of values || []) {
            let key = normalizeText(value);
            if (!key) continue;
            map.set(key, (map.get(key) || 0) + 1);
        }
        return map;
    }

    function getSortingExpectedByCategory(categorizedAnswers) {
        let expected = new Map();

        for (let pair of categorizedAnswers || []) {
            let category = normalizeCategoryLabel(pair.category);
            let answer = normalizeText(pair.answer);
            if (!category || !answer) continue;

            if (!expected.has(category)) {
                expected.set(category, []);
            }
            expected.get(category).push(answer);
        }

        return expected;
    }

    async function moveUnexpectedSortingItemsToPool(categorizedAnswers) {
        let expectedByCategory = getSortingExpectedByCategory(categorizedAnswers);
        let movedAny = false;

        for (let category of getSortingSnapshot().categories) {
            let expectedCounts = buildTextCountMap(expectedByCategory.get(category.label) || []);
            let currentCounts = new Map();

            let itemNodes = Array.from(category.node.querySelectorAll('.draggable.drag_item')).filter(isReallyVisible);
            for (let node of itemNodes) {
                let text = normalizeText(node.innerText || node.textContent || '');
                if (!text) continue;

                let nextCount = (currentCounts.get(text) || 0) + 1;
                currentCounts.set(text, nextCount);

                if (nextCount <= (expectedCounts.get(text) || 0)) {
                    continue;
                }

                let view = getJQueryMvcView(node);
                if (!view || typeof view.moveToPool !== 'function') {
                    continue;
                }

                try {
                    view.moveToPool();
                    movedAny = true;
                } catch (e) {}

                await new Promise(r => setTimeout(r, 80));
            }
        }

        return movedAny;
    }

    async function solveSortingCategorization(categorizedAnswers) {
        if (!categorizedAnswers || categorizedAnswers.length === 0) return false;
        if (getSortingSnapshot().categories.length === 0) return false;

        // Cambridge llena estas columnas por orden de categoria y casilla.
        // Si respetamos ese orden, basta activar la ficha correcta.
        for (let pair of categorizedAnswers) {
            if (isSortingAnswerInCategory(pair.category, pair.answer)) continue;

            await clickSortingAnswerInOrder(pair.answer);
            await new Promise(r => setTimeout(r, 110));
        }

        let fullySolved = categorizedAnswers.every(pair => isSortingAnswerInCategory(pair.category, pair.answer));
        if (fullySolved) return true;

        // Si algo quedo mal ubicado o la pantalla ya venia parcialmente resuelta,
        // usamos el movimiento interno de Cambridge para recolocar ficha por ficha.
        for (let pass = 0; pass < 4; pass++) {
            let movedThisPass = false;

            let cleaned = await moveUnexpectedSortingItemsToPool(categorizedAnswers);
            if (cleaned) {
                movedThisPass = true;
            }

            for (let pair of categorizedAnswers) {
                if (isSortingAnswerInCategory(pair.category, pair.answer)) continue;

                let ok = await moveSortingAnswerToCategory(pair.category, pair.answer);
                if (ok) {
                    movedThisPass = true;
                }
                await new Promise(r => setTimeout(r, 90));
            }

            if (categorizedAnswers.every(pair => isSortingAnswerInCategory(pair.category, pair.answer))) {
                return true;
            }

            if (!movedThisPass) break;
        }

        return categorizedAnswers.every(pair => isSortingAnswerInCategory(pair.category, pair.answer));
    }

    async function dragTo(source, target) {
        if(!source || !target) return;
        source.scrollIntoView({behavior: "auto", block: "center"});
        const dt = new DataTransfer();
        ['pointerdown', 'mousedown'].forEach(evt => source.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window})));
        source.dispatchEvent(new DragEvent('dragstart', {bubbles:true, cancelable:true, dataTransfer: dt}));
        await new Promise(r => setTimeout(r, 50));
        target.scrollIntoView({behavior: "auto", block: "center"});
        ['dragenter', 'dragover'].forEach(evt => target.dispatchEvent(new DragEvent(evt, {bubbles:true, cancelable:true, dataTransfer: dt})));
        await new Promise(r => setTimeout(r, 50));
        target.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer: dt}));
        source.dispatchEvent(new DragEvent('dragend', {bubbles:true, cancelable:true, dataTransfer: dt}));
        ['pointerup', 'mouseup'].forEach(evt => source.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window})));
    }

    // Hack para asegurar que el documento detecte foco (por si el usuario está en otra pestaña)
    Object.defineProperty(document, 'hasFocus', { value: () => true, writable: true });

    async function fillFast() {
        let doneCount = 0;

        let rawAnswers = answers
            .map(a => (typeof a === 'string' ? a.trim() : ''))
            .filter(Boolean);

        let categorizedAnswers = rawAnswers
            .map(parseCategorizedAnswer)
            .filter(Boolean);

        let plainAnswers = rawAnswers.map(a => {
            let parsed = parseCategorizedAnswer(a);
            return parsed ? parsed.answer : a;
        });

        answers = plainAnswers;

        // ===== 1: DROPDOWNS =====
        let drops = sortByDocumentOrder(
            dedupeElements(
                Array.from(document.querySelectorAll('.drop-label.listbox__label, .listbox__label, [role="combobox"], button[aria-haspopup], [aria-haspopup="listbox"]'))
                    .filter(isReallyVisible)
                    .map(getDropdownRoot)
            )
        );
        if (drops.length > 0) {
            let solvedCount = 0;
            let attempts = Math.min(drops.length, answers.length);
            for (let i = 0; i < attempts; i++) {
                let ok = await selectDropdownAnswer(drops[i], answers[i]);
                if (ok) {
                    solvedCount++;
                    doneCount++;
                }
                await new Promise(r => setTimeout(r, 110));
            }

            if (solvedCount > 0 && solvedCount >= attempts) {
                callback(true);
                return;
            }
        }

        // ===== 2: RADIO / MULTIPLE CHOICE =====
        let rawRadioOpts = Array.from(
            document.querySelectorAll('input[type="radio"], input[type="checkbox"], label, [role="radio"], [role="checkbox"], .option, .choice')
        ).filter(isVisible);
        let radioOpts = [];
        let seenChoiceNodes = new Set();

        for (let el of rawRadioOpts) {
            let input = getAssociatedInput(el);
            let roleNode = getChoiceRoleNode(el);
            let baseNode = el;

            if (input && input.labels && input.labels[0]) baseNode = input.labels[0];
            else if (roleNode) baseNode = roleNode;
            else if (el.closest && el.closest('label')) baseNode = el.closest('label');

            let text = getChoiceText(baseNode);
            if (!text) continue;

            let uniqueNode = input || roleNode || baseNode;
            if (seenChoiceNodes.has(uniqueNode)) continue;
            seenChoiceNodes.add(uniqueNode);

            radioOpts.push({
                node: baseNode,
                input: input,
                roleNode: roleNode,
                text: text,
                type: getChoiceType(baseNode),
                groupKey: getChoiceGroupKey(baseNode)
            });
        }

        if (radioOpts.length >= 2) {
            let ok = false;
            let usedChoiceNodes = new Set();
            let occupiedRadioGroups = new Set();
            let groupOrder = [];
            let groupOrderMap = new Map();

            for (let opt of radioOpts) {
                if (groupOrderMap.has(opt.groupKey)) continue;
                groupOrderMap.set(opt.groupKey, groupOrder.length);
                groupOrder.push(opt.groupKey);
            }

            for (let i = 0; i < answers.length; i++) {
                let ansLow = normalizeText(answers[i]);
                if (!ansLow) continue;

                let matches = radioOpts.filter(opt => {
                    if (!(opt.text === ansLow || opt.text.includes(ansLow) || ansLow.includes(opt.text))) return false;

                    let uniqueNode = opt.input || opt.roleNode || opt.node;
                    if (usedChoiceNodes.has(uniqueNode)) return false;

                    if (opt.type !== 'checkbox' && occupiedRadioGroups.has(opt.groupKey) && !isChoiceSelected(opt.node)) {
                        return false;
                    }

                    return true;
                });

                if (matches.length === 0) {
                    matches = radioOpts.filter(opt => {
                        if (!(opt.text === ansLow || opt.text.includes(ansLow) || ansLow.includes(opt.text))) return false;
                        let uniqueNode = opt.input || opt.roleNode || opt.node;
                        return !usedChoiceNodes.has(uniqueNode);
                    });
                }

                matches.sort((a, b) => {
                    let aExact = a.text === ansLow ? 0 : 1;
                    let bExact = b.text === ansLow ? 0 : 1;
                    if (aExact !== bExact) return aExact - bExact;

                    let aGroupRank = groupOrderMap.has(a.groupKey) ? groupOrderMap.get(a.groupKey) : 9999;
                    let bGroupRank = groupOrderMap.has(b.groupKey) ? groupOrderMap.get(b.groupKey) : 9999;
                    if (aGroupRank !== bGroupRank) return aGroupRank - bGroupRank;

                    let aSelected = isChoiceSelected(a.node) ? 1 : 0;
                    let bSelected = isChoiceSelected(b.node) ? 1 : 0;
                    return aSelected - bSelected;
                });

                let target = matches[0];
                if (target) {
                    let selected = isChoiceSelected(target.node) || clickChoice(target);
                    if (selected) {
                        let uniqueNode = target.input || target.roleNode || target.node;
                        usedChoiceNodes.add(uniqueNode);
                        if (target.type !== 'checkbox') {
                            occupiedRadioGroups.add(target.groupKey);
                        }
                        doneCount++;
                        ok = true;
                        await new Promise(r => setTimeout(r, 140));
                    }
                }
            }
            if (ok) { callback(doneCount > 0); return; }
        }

        // ===== 3: SORTING / CATEGORISATION =====
        if (categorizedAnswers.length > 0 && getSortingSnapshot().categories.length > 0) {
            let sortingOk = await solveSortingCategorization(categorizedAnswers);
            callback(sortingOk);
            return;
        }

        // ===== 4: WORD BANK (click simple, Cambridge auto-place) =====
        // REGLA CRITICA: solo click en la palabra, NO click en el gap despues.
        // Clickear el gap luego de la palabra causa deseleccion.
        let clickedCount = 0;
        for (let i = 0; i < answers.length; i++) {
            let ansLow = normalizeText(answers[i]);
            if (!ansLow || ansLow.length < 2) continue;

            let ok = await clickWordBankAnswer(answers[i]);
            if (ok) {
                doneCount++;
                clickedCount++;
            }
            await new Promise(r => setTimeout(r, 220));
        }
        if (clickedCount > 0 && clickedCount >= answers.length) { callback(true); return; }

        // ===== 5: TEXT INPUTS =====
        let tIn = Array.from(document.querySelectorAll('input[type="text"],textarea,[contenteditable="true"]')).filter(e => e.offsetParent !== null && !e.readOnly && !e.disabled);
        if (tIn.length > 0 && tIn.length >= answers.length) {
            for(let i=0; i<answers.length; i++){
                let inp = tIn[i];
                inp.focus(); inp.dispatchEvent(new Event('focus', {bubbles:true}));
                let ns = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                if(ns && ns.set){ ns.set.call(inp, answers[i]); } else { inp.value = answers[i]; }
                ['input','change','blur'].forEach(ev => inp.dispatchEvent(new Event(ev, {bubbles:true})));
                doneCount++; await new Promise(r => setTimeout(r,90));
            }
            callback(doneCount > 0); return;
        }

        // ===== 6: SELECT NATIVO =====
        let sels = Array.from(document.querySelectorAll('select')).filter(e => e.offsetParent !== null);
        if(sels.length > 0){
            let ok = false;
            for(let i=0; i<Math.min(sels.length, answers.length); i++){
                let al = answers[i].trim().toLowerCase();
                let op = Array.from(sels[i].options).find(o => o.innerText.trim().toLowerCase() === al);
                if(op){ sels[i].value = op.value; sels[i].dispatchEvent(new Event('change',{bubbles:true})); doneCount++; ok=true; }
            }
            if(ok){ callback(doneCount > 0); return; }
        }

        callback(doneCount > 0);
    }
    fillFast();
    """
    if frame_elemento is not None:
        try:
            driver.switch_to.frame(frame_elemento)
        except Exception:
            # Si el frame es stale (viejo), intentamos buscarlo de nuevo por RAM
            _, nuevo_frame = get_ajax_data_directly(driver)
            if nuevo_frame:
                try:
                    driver.switch_to.frame(nuevo_frame)
                except:
                    return False
            else:
                return False
        
    # Usamos execute_async_script para que Python se congele hasta que los clics JS terminen secuencialmente
    driver.set_script_timeout(20) # Antes 10, ahora 20 por el scroll
    try:
        exito = driver.execute_async_script(js_code, respuestas_planas)
    except Exception as e:
        print(f"   ⚠️ Error en inyección JS: {e}")
        exito = False
    
    try:
        driver.switch_to.default_content()
    except:
        pass
        
    return exito


# =========================================
# 🔹 CHECK ANSWERS + NEXT (CAMBRIDGE ONE)
# =========================================

def click_check_answers(driver, frame_elemento):
    """Hace click en el botón 'Check Answers' de Cambridge One."""
    js_code = r"""
    let callback = arguments[0];
    async function doCheck() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        let btn = document.querySelector('a.btn.btn-check') 
               || document.querySelector('button.green-btn')
               || document.querySelector('a[data-event="check"]')
               || document.querySelector('button.btn-check')
               || document.querySelector('[class*="check"]');
        
        if (!btn) {
            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                return (text.includes('check') || text.includes('validar')) && b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 700));
            callback(true);
        } else {
            callback(false);
        }
    }
    doCheck();
    """
    return _ejecutar_en_frame(driver, frame_elemento, js_code)


def click_forward(driver, frame_elemento):
    """Hace click en el botón 'Forward/Next' de Cambridge One."""
    js_code = r"""
    let callback = arguments[0];
    async function doNext() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        let btn = document.querySelector('a#btn_forward')
               || document.querySelector('a.btn.btn-next')
               || document.querySelector('button.btn-next')
               || document.querySelector('a.btn.btn-forward')
               || document.querySelector('[data-action="forward"]');
        
        if (!btn) {
            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                let aria = (b.getAttribute('aria-label') || '').toLowerCase();
                let cls = (b.className || '').toLowerCase();
                return (text === 'next' || aria.includes('forward') || aria.includes('next') || 
                        cls.includes('forward') || cls.includes('next')) && 
                       b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 850));
            callback(true);
        } else {
            callback(false);
        }
    }
    doNext();
    """
    return _ejecutar_en_frame(driver, frame_elemento, js_code)


def _ejecutar_en_frame(driver, frame_elemento, js_code):
    """Ejecuta JS primero dentro del iframe, y si falla, en el parent."""
    if frame_elemento:
        try:
            driver.switch_to.frame(frame_elemento)
        except:
            _, nuevo_frame = get_ajax_data_directly(driver)
            if nuevo_frame:
                try: driver.switch_to.frame(nuevo_frame)
                except: pass
    
    driver.set_script_timeout(10)
    result = False
    try:
        result = driver.execute_async_script(js_code)
    except:
        pass
    
    try: driver.switch_to.default_content()
    except: pass
    
    # Si no funcionó dentro del iframe, intentar en el parent
    if not result:
        driver.set_script_timeout(10)
        try:
            result = driver.execute_async_script(js_code)
        except:
            pass
    
    return result


# =========================================
# 🔹 CLICK "NEXT ACTIVITY" EN PANTALLA DE RESULTADOS
# =========================================

def click_next_activity(driver):
    """Hace click en 'Next activity' en la pantalla de resultados (Amazing!)."""
    # Este botón está FUERA del iframe, en el documento principal
    try:
        driver.switch_to.default_content()
    except:
        pass
    
    js_code = r"""
    let callback = arguments[0];
    async function doNextActivity() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        // Buscar botón "Next activity" en el documento principal
        let btn = document.querySelector('a.nextActivityBtn')
               || document.querySelector('button.nextActivityBtn')
               || document.querySelector('[class*="nextActivity"]');
        
        if (!btn) {
            // Buscar por texto
            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                return text.includes('next activity') && b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 850));
            callback(true);
        } else {
            callback(false);
        }
    }
    doNextActivity();
    """
    
    driver.set_script_timeout(10)
    result = False
    
    # Intentar en main document
    try:
        result = driver.execute_async_script(js_code)
    except:
        pass
    
    if not result:
        # Intentar dentro del iframe
        _, frame = get_ajax_data_directly(driver)
        if frame:
            try:
                driver.switch_to.frame(frame)
                result = driver.execute_async_script(js_code)
                driver.switch_to.default_content()
            except:
                try: driver.switch_to.default_content()
                except: pass
    
    return result


def click_next_clickable_module(driver):
    """Hace click en el siguiente mÃ³dulo/actividad clicable."""
    try:
        driver.switch_to.default_content()
    except:
        pass

    js_code = r"""
    let callback = arguments[0];
    async function doNextClickableModule() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }

        function normalizeText(text) {
            return (text || '').replace(/\s+/g, ' ').trim().toLowerCase();
        }

        function isVisible(el) {
            return !!el && el.offsetParent !== null;
        }

        function hasLockedIcon(el) {
            if (!el || !el.querySelector) return false;
            return !!el.querySelector(
                '.nemo-lock, .locked, .lock-icon, [aria-label*="lock"], [title*="lock"], [class*="lock"]'
            );
        }

        function findDirectNextButton() {
            let selectors = [
                'a[qid="resultScreen-1"]',
                'button[qid="resultScreen-1"]',
                'a.nextActivityBtn',
                'button.nextActivityBtn',
                '[class*="nextActivity"]',
                '.btn.btn-primary'
            ];

            for (let selector of selectors) {
                let node = document.querySelector(selector);
                if (!node || !isVisible(node)) continue;

                let text = normalizeText(node.innerText || node.textContent || '');
                if (!text || text.includes('next activity') || text === 'next') {
                    return node;
                }
            }

            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]')).filter(isVisible);
            return allBtns.find(b => {
                let text = normalizeText(b.textContent || '');
                return text.includes('next activity') || text === 'next';
            }) || null;
        }

        async function openSidebarIfNeeded() {
            let visibleItems = Array.from(document.querySelectorAll('a.activity-name-container')).filter(isVisible);
            if (visibleItems.length > 0) {
                return true;
            }

            let openers = [
                document.getElementById('selectedActivitySidebarBtn'),
                document.querySelector('.open-sidebar-btn'),
                document.querySelector('.toc-hamburger-btn')
            ].filter(Boolean);

            for (let opener of openers) {
                try {
                    supremeClick(opener);
                    if (typeof opener.click === 'function') opener.click();
                } catch (e) {}

                await new Promise(r => setTimeout(r, 320));
                visibleItems = Array.from(document.querySelectorAll('a.activity-name-container')).filter(isVisible);
                if (visibleItems.length > 0) {
                    return true;
                }
            }

            return false;
        }

        function findNextSidebarItem() {
            let items = Array.from(document.querySelectorAll('a.activity-name-container')).filter(isVisible);
            if (items.length === 0) return null;

            let activeIndex = items.findIndex(item => item.classList.contains('active'));

            if (activeIndex === -1) {
                let currentTitle = normalizeText(
                    (document.getElementById('selectedActivitySidebarBtn') && document.getElementById('selectedActivitySidebarBtn').textContent) || ''
                ).replace(/\s+100%$/, '');

                if (currentTitle) {
                    activeIndex = items.findIndex(item => {
                        let itemText = normalizeText(item.textContent || '').replace(/\s+100%$/, '');
                        return itemText === currentTitle || itemText.includes(currentTitle) || currentTitle.includes(itemText);
                    });
                }
            }

            if (activeIndex === -1) return null;

            for (let i = activeIndex + 1; i < items.length; i++) {
                let item = items[i];
                let text = normalizeText(item.textContent || '');
                if (!text || hasLockedIcon(item)) continue;
                return item;
            }

            return null;
        }

        let btn = findDirectNextButton();
        if (btn) {
            supremeClick(btn);
            if (typeof btn.click === 'function') btn.click();
            await new Promise(r => setTimeout(r, 900));
            callback(true);
            return;
        }

        let sidebarReady = await openSidebarIfNeeded();
        if (sidebarReady) {
            let nextItem = findNextSidebarItem();
            if (nextItem) {
                supremeClick(nextItem);
                if (typeof nextItem.click === 'function') nextItem.click();
                await new Promise(r => setTimeout(r, 1000));
                callback(true);
                return;
            }
        }

        callback(false);
    }
    doNextClickableModule();
    """

    driver.set_script_timeout(10)
    result = False

    try:
        result = driver.execute_async_script(js_code)
    except:
        pass

    if not result:
        _, frame = get_ajax_data_directly(driver)
        if frame:
            try:
                driver.switch_to.frame(frame)
                result = driver.execute_async_script(js_code)
                driver.switch_to.default_content()
            except:
                try: driver.switch_to.default_content()
                except: pass

    return result


def click_next_button_bottom(driver, frame_elemento):
    """Hace click en el botón azul 'Next' que aparece al fondo de pantallas de presentación."""
    js_code = r"""
    let callback = arguments[0];
    async function doNext() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        //Buscar botón "Next" azul al fondo
        let btn = null;
        let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"], input[type="button"]'));
        
        //Primero intentar selector específico de Cambridge
        btn = document.querySelector('a.btn.btn-next-activity')
           || document.querySelector('a.btn-next')
           || document.querySelector('button.btn-next');
        
        if (!btn) {
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                return text === 'next' && b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 650));
            callback(true);
        } else {
            callback(false);
        }
    }
    doNext();
    """
    return _ejecutar_en_frame(driver, frame_elemento, js_code)


def detectar_pantalla_resultados(driver):
    """Detecta si estamos en la pantalla de resultados (Amazing!/Well done!/etc.)"""
    try:
        driver.switch_to.default_content()
    except:
        pass
    
    # Buscar en ambos contextos
    js_code = r"""
    let text = document.body ? document.body.innerText : '';
    return text.includes('You scored') || text.includes('Amazing') || 
           text.includes('Well done') || text.includes('Good try') ||
           text.includes('Next activity') || text.includes('Start again');
    """
    
    try:
        # Buscar en main document
        result = driver.execute_script(js_code)
        if result:
            return True
    except:
        pass
    
    # Buscar en iframe
    _, frame = get_ajax_data_directly(driver)
    if frame:
        try:
            driver.switch_to.frame(frame)
            result = driver.execute_script(js_code)
            driver.switch_to.default_content()
            if result:
                return True
        except:
            try: driver.switch_to.default_content()
            except: pass
    
    return False


def get_current_activity_label(driver):
    """Obtiene el nombre visible de la actividad actual en Cambridge."""
    try:
        driver.switch_to.default_content()
    except:
        pass

    js_code = r"""
    let node = document.getElementById('selectedActivitySidebarBtn')
            || document.querySelector('.open-sidebar.open-sidebar-btn')
            || document.querySelector('.open-sidebar-btn');
    if (node) {
        return (node.textContent || '').replace(/\s+/g, ' ').trim();
    }
    return (document.title || '').replace(/\s+/g, ' ').trim();
    """

    try:
        return (driver.execute_script(js_code) or "").strip()
    except:
        return ""


def get_screen_signature(driver):
    """Devuelve una firma ligera del contenido visible para detectar cambios de pantalla."""
    def _read_text():
        try:
            return driver.execute_script(
                "return (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').trim().slice(0, 1800);"
            ) or ""
        except:
            return ""

    try:
        driver.switch_to.default_content()
    except:
        pass

    parts = []
    main_text = _read_text()
    if main_text:
        parts.append(main_text)

    _, frame = get_ajax_data_directly(driver)
    if frame:
        try:
            driver.switch_to.frame(frame)
            frame_text = _read_text()
            if frame_text and frame_text not in parts:
                parts.append(frame_text)
        except:
            pass
        finally:
            try:
                driver.switch_to.default_content()
            except:
                pass

    return " || ".join(parts)


def wait_for_screen_change(driver, previous_signature, timeout=2.2, poll_interval=0.08):
    """Espera hasta que cambie el contenido visible o aparezca la pantalla final."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if detectar_pantalla_resultados(driver):
            return True

        current_signature = get_screen_signature(driver)
        if current_signature and current_signature != previous_signature:
            return True

        time.sleep(poll_interval)

    return detectar_pantalla_resultados(driver)


def wait_for_data_or_results(driver, timeout=2.3, poll_interval=0.08):
    """Espera datos extraibles del ejercicio o la pantalla de resultados."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if detectar_pantalla_resultados(driver):
            return True, None, None

        data_dict, frame_elemento = get_ajax_data_directly(driver)
        if data_dict:
            return False, data_dict, frame_elemento

        time.sleep(poll_interval)

    return detectar_pantalla_resultados(driver), None, None


def wait_for_next_activity_ready(driver, previous_label="", previous_url="", timeout=7.0, poll_interval=0.12):
    """Espera a que cargue la siguiente actividad o cambie el contexto actual."""
    deadline = time.time() + timeout
    previous_label = (previous_label or "").strip()
    previous_url = (previous_url or "").strip()

    while time.time() < deadline:
        try:
            current_url = driver.current_url
        except:
            current_url = ""

        current_label = get_current_activity_label(driver)

        if previous_url and current_url and current_url != previous_url:
            return True
        if previous_label and current_label and current_label != previous_label:
            return True

        if detectar_pantalla_resultados(driver):
            return True

        data_dict, _ = get_ajax_data_directly(driver)
        if data_dict:
            return True

        time.sleep(poll_interval)

    return False


# =========================================
# 🔹 RESOLVER UN EJERCICIO COMPLETO
# =========================================

def resolver_ejercicio(driver):
    """Resuelve un ejercicio completo (todas sus pantallas). 
    Retorna True si se completó exitosamente."""
    
    print("🔍 Extrayendo data del ejercicio...")
    on_results, data_dict, frame_elemento = wait_for_data_or_results(driver, timeout=1.2)
    
    if on_results:
        print("  [OK] Pantalla de resultados detectada!")
        return True
    
    if not data_dict:
        print("❌ No se encontró data. Esperando más tiempo...")
        on_results, data_dict, frame_elemento = wait_for_data_or_results(driver, timeout=1.8)
        if on_results:
            print("  [OK] Pantalla de resultados detectada!")
            return True
        if not data_dict:
            print("❌ No hay data disponible. Puede ser una pantalla sin ejercicio.")
            if detectar_pantalla_resultados(driver):
                print("  [OK] Pantalla de resultados detectada!")
                return True
            return False
    
    estructura = []
    if "LearningObjectInfo.xml" in data_dict:
        estructura = parse_learning_object(data_dict["LearningObjectInfo.xml"])
    
    if not estructura:
        print("⚠️ No se detectaron pantallas en este ejercicio.")
        return False

    # Preparar respuestas por pantalla
    respuestas_por_pantalla = []
    for idx, pantalla in enumerate(estructura):
        nombre = pantalla["archivo"]
        tipo = pantalla["tipo"]
        respuestas = []
        if nombre in data_dict:
            q = parse_question(data_dict[nombre], nombre, tipo)
            if q:
                if "sub_preguntas" in q:
                    for sub in q["sub_preguntas"]:
                        respuestas.extend(sub["correctas"])
                else:
                    respuestas.extend(q.get("correctas", []))
        respuestas = [r for r in respuestas if isinstance(r, str) and r.strip()]
        respuestas_por_pantalla.append(respuestas)
    
    total = len(estructura)
    con_respuestas = sum(1 for r in respuestas_por_pantalla if r)
    print(f"🏆 {total} pantallas detectadas")
    
    # Pre-scrolling para asegurar que todo cargue
    try: driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    except: pass
    time.sleep(0.15)
    try: driver.execute_script("window.scrollTo(0, 0);")
    except: pass
    time.sleep(0.15)
    
    # ===== AUTO-RESOLVER TODAS LAS PANTALLAS =====
    for idx in range(total):
        if detectar_pantalla_resultados(driver):
            print("  [OK] Pantalla de resultados detectada!")
            return True

        pantalla = estructura[idx]
        respuestas = respuestas_por_pantalla[idx]
        
        if not respuestas:
            print(f"  ⏭️  Pantalla {idx+1}/{total}: Presentación → Avanzando...")
            # Intentar primero el forward, si no funciona, el botón "Next" azul
            previous_signature = get_screen_signature(driver)
            fwd_ok = click_forward(driver, frame_elemento)
            if not fwd_ok:
                click_next_button_bottom(driver, frame_elemento)
            wait_for_screen_change(driver, previous_signature, timeout=1.8)
            if detectar_pantalla_resultados(driver):
                print("  [OK] Pantalla de resultados detectada!")
                return True
            _, frame_elemento = get_ajax_data_directly(driver)
            continue
        
        print(f"\n  🎯 Pantalla {idx+1}/{total}: Resolviendo ({len(respuestas)} respuestas)")
        for i, r in enumerate(respuestas, 1):
            print(f"     [{i}] {r}")
        
        # Paso 1: Llenar respuestas
        time.sleep(0.15)
        exito = resolver_pantalla_js(driver, frame_elemento, respuestas)
        if exito:
            print(f"     ✅ Respuestas ingresadas")
        else:
            print(f"     ⚠️ Posible fallo al ingresar respuestas")
        
        # Paso 2: Click en Check Answers
        time.sleep(0.18)
        check_ok = click_check_answers(driver, frame_elemento)
        if check_ok:
            print(f"     ✅ Check Answers clickeado")
        else:
            print(f"     ⚠️ No se encontró botón Check Answers")
        
        # Paso 3: Avanzar
        previous_signature = get_screen_signature(driver)
        time.sleep(0.18)
        next_ok = click_forward(driver, frame_elemento)
        if next_ok:
            print(f"     ➡️  Avanzando...")
        else:
            # Intentar botón "Next" azul como fallback
            click_next_button_bottom(driver, frame_elemento)
        
        wait_for_screen_change(driver, previous_signature, timeout=2.4)
        if detectar_pantalla_resultados(driver):
            print("  [OK] Pantalla de resultados detectada!")
            return True
        _, frame_elemento = get_ajax_data_directly(driver)
    
    # Verificar si llegamos a la pantalla de resultados
    time.sleep(0.2)
    if detectar_pantalla_resultados(driver):
        print("  🌟 ¡Pantalla de resultados detectada!")
        return True
    
    # Si no se detectó automáticamente, intentar avanzar una vez más
    previous_signature = get_screen_signature(driver)
    click_forward(driver, frame_elemento)
    wait_for_screen_change(driver, previous_signature, timeout=1.4)
    click_next_button_bottom(driver, frame_elemento)
    wait_for_screen_change(driver, previous_signature, timeout=1.4)
    
    return detectar_pantalla_resultados(driver)


# =========================================
# 🔹 PROCESAR TODO
# =========================================

def main():
    print("=" * 60)
    print("Bot Cambridge One - Resolucion Automatica Multi-Ejercicio")
    print("=" * 60)
    options = Options()
    
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    
    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        print("\nERROR: No se pudo conectar a Brave.")
        print("TIP: Para usar el Brave donde tienes tus cuentas, debes cerrarlo y abrirlo en 'modo depuracion'.")
        print("He creado un archivo llamado 'lanzar_brave_debug.bat' en esta carpeta.")
        print("Haz doble click a 'lanzar_brave_debug.bat' y luego vuelve a ejecutar este bot.\n")
        return
    
    print("\nConectado exitosamente a tu Brave principal!")
    print("1) Anda a la pestaña donde tienes abierta tu actividad de Cambridge One.")
    print("2) Navega a la PRIMERA pantalla del ejercicio.")
    print("3) Manten abierta esta consola.\n")

    while True:
        accion = input("\nPresiona ENTER para resolver TODOS los ejercicios automaticamente (o 'q' para salir): ").strip()
        if accion.lower() == 'q':
            break
        
        os.system('cls' if os.name == 'nt' else 'clear')
        print("🔍 Buscando la pestaña de Cambridge...")
        
        encontrado = False
        handles = driver.window_handles
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                if "cambridgeone.org" in driver.current_url:
                    encontrado = True
                    break
            except:
                continue
                
        if not encontrado:
            print("No estas en la pestaña de Cambridge One! Ve a la actividad y presiona ENTER de nuevo.")
            continue
        
        ejercicio_num = 0
        seguir = True
        
        while seguir:
            ejercicio_num += 1
            print(f"\n{'='*50}")
            print(f"EJERCICIO #{ejercicio_num}")
            print(f"{'='*50}")
            
            completado = resolver_ejercicio(driver)
            
            if completado:
                print(f"\n¡Ejercicio #{ejercicio_num} COMPLETADO!")
                
                # Intentar ir al siguiente ejercicio
                print("Buscando siguiente modulo/actividad clicable...")
                previous_label = get_current_activity_label(driver)
                previous_url = driver.current_url
                time.sleep(0.15)
                next_activity_ok = click_next_clickable_module(driver)
                
                if next_activity_ok:
                    print("Navegando al siguiente ejercicio...")
                    activity_ready = wait_for_next_activity_ready(driver, previous_label, previous_url, timeout=6.0)
                    
                    # Verificar que se cargó un nuevo ejercicio (tiene ajaxData)
                    test_data = activity_ready
                    if test_data:
                        print("Nuevo ejercicio detectado. Continuando...")
                        continue  # Resolver el siguiente ejercicio
                    else:
                        print("No se detecto un nuevo ejercicio. Puede ser una presentacion.")
                        # Intentar una vez más después de esperar
                        test_data = wait_for_next_activity_ready(driver, previous_label, previous_url, timeout=4.0)
                        if test_data:
                            continue
                        else:
                            print("No hay mas ejercicios con data extraible.")
                            seguir = False
                else:
                    print("No se encontro un siguiente modulo/actividad clicable. Puede que hayas terminado la leccion.")
                    seguir = False
            else:
                print(f"No se pudo completar el ejercicio #{ejercicio_num}.")
                print("   Puede ser una presentación o un tipo de ejercicio no soportado.")
                
                # Intentar avanzar de todas formas
                print("Intentando ir al siguiente modulo/actividad...")
                previous_label = get_current_activity_label(driver)
                previous_url = driver.current_url
                time.sleep(0.15)
                next_activity_ok = click_next_clickable_module(driver)
                if next_activity_ok:
                    print("Navegando al siguiente ejercicio...")
                    wait_for_next_activity_ready(driver, previous_label, previous_url, timeout=6.0)
                    continue
                else:
                    seguir = False
        
        print("\n" + "=" * 50)
        print(f"SESION TERMINADA: {ejercicio_num} ejercicio(s) procesados")
        print("=" * 50)
        print("\nNavega a otro ejercicio y presiona ENTER para continuar.")

if __name__ == "__main__":
    main()
