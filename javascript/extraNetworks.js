function toggleCss(key, css, enable) {
    let style = document.getElementById(key);
    if (enable && !style) {
        style = document.createElement("style");
        style.id = key;
        style.type = "text/css";
        document.head.appendChild(style);
    }
    if (style && !enable) {
        document.head.removeChild(style);
    }
    if (style) {
        style.innerHTML == "";
        style.appendChild(document.createTextNode(css));
    }
}

function setupExtraNetworksForTab(tabname) {
    function registerPrompt(tabname, id) {
        let textarea = gradioApp().querySelector("#" + id + " > label > textarea");

        if (!activePromptTextarea[tabname]) {
            activePromptTextarea[tabname] = textarea;
        }

        textarea.addEventListener("focus", function () {
            activePromptTextarea[tabname] = textarea;
        });
    }

    let tabnav = gradioApp().querySelector(
        "#" + tabname + "_extra_tabs > div.tab-nav",
    );
    let controlsDiv = document.createElement("DIV");
    controlsDiv.classList.add("extra-networks-controls-div");
    tabnav.appendChild(controlsDiv);
    tabnav.insertBefore(controlsDiv, null);

    let this_tab = gradioApp().querySelector("#" + tabname + "_extra_tabs");
    this_tab
        .querySelectorAll(":scope > [id^='" + tabname + "_']")
        .forEach(function (elem) {
            // tabname_full = {tabname}_{extra_networks_tabname}
            let tabname_full = elem.id;
            let search = gradioApp().querySelector(
                "#" + tabname_full + "_extra_search",
            );
            let sort_dir = gradioApp().querySelector(
                "#" + tabname_full + "_extra_sort_dir",
            );
            let refresh = gradioApp().querySelector(
                "#" + tabname_full + "_extra_refresh",
            );
            let currentSort = "";

            // If any of the buttons above don't exist, we want to skip this iteration of the loop.
            if (!search || !sort_dir || !refresh) {
                return; // `return` is equivalent of `continue` but for forEach loops.
            }

            let applyFilter = function (force) {
                let searchTerm = search.value.toLowerCase();

                // get UI preset
                radioUI = gradioApp().querySelector("#forge_ui_preset");
                radioButtons = radioUI.getElementsByTagName("input");
                UIresult = 3; //  default to 'all'
                for (i = 0; i < radioButtons.length; i++) {
                    if (radioButtons[i].checked) {
                        UIresult = i;
                    }
                }

                gradioApp()
                    .querySelectorAll("#" + tabname + "_extra_tabs div.card")
                    .forEach(function (elem) {
                        let searchOnly = elem.querySelector(".search_only");
                        let text = Array.prototype.map
                            .call(
                                elem.querySelectorAll(".search_terms, .description"),
                                function (t) {
                                    return t.textContent.toLowerCase();
                                },
                            )
                            .join(" ");

                        let visible = true;
                        if (searchOnly && searchTerm.length < 4) visible = false;

                        splitSearch = searchTerm.split(" ");
                        splitSearch.forEach(function (partial) {
                            if (text.indexOf(partial) == -1) visible = false;
                        });

                        sdversion = elem.getAttribute("data-sort-sdversion");
                        if (sdversion == null);
                        else if (sdversion == "SdVersion.Unknown");
                        else if (opts.lora_filter_disabled == True);
                        else if (UIresult == 3); //  'all'
                        else if (UIresult == 0) {
                            //  'sd'
                            if (sdversion != "SdVersion.SD1")
                                visible = false;
                        } else if (UIresult == 1) {
                            //  'xl'
                            if (sdversion != "SdVersion.SDXL") visible = false;
                        } else if (UIresult == 2) {
                            //  'flux'
                            if (sdversion != "SdVersion.Flux") visible = false;
                        }

                        if (visible) {
                            elem.classList.remove("hidden");
                        } else {
                            elem.classList.add("hidden");
                        }
                    });

                applySort(force);
            };

            let applySort = function (force) {
                let cards = gradioApp().querySelectorAll(
                    "#" + tabname_full + " div.card",
                );
                let parent = gradioApp().querySelector("#" + tabname_full + "_cards");
                let reverse = sort_dir.dataset.sortdir == "Descending";
                let activeSearchElem = gradioApp().querySelector(
                    "#" +
                    tabname_full +
                    "_controls .extra-network-control--sort.extra-network-control--enabled",
                );
                let sortKey = activeSearchElem
                    ? activeSearchElem.dataset.sortkey
                    : "default";
                let sortKeyDataField =
                    "sort" + sortKey.charAt(0).toUpperCase() + sortKey.slice(1);
                let sortKeyStore =
                    sortKey + "-" + sort_dir.dataset.sortdir + "-" + cards.length;

                if (sortKeyStore == currentSort && !force) {
                    return;
                }
                currentSort = sortKeyStore;

                let sortedCards = Array.from(cards);
                sortedCards.sort(function (cardA, cardB) {
                    // STEP 1: Sort by pinned status first (pinned comes before unpinned)
                    let pinnedA = parseInt(cardA.dataset.sortPinned) || 0;
                    let pinnedB = parseInt(cardB.dataset.sortPinned) || 0;
                    
                    if (pinnedA !== pinnedB) {
                        return pinnedB - pinnedA;  // Higher value (1=pinned) comes first
                    }
                    
                    // STEP 2: If both have same pin status, use current sort field
                    let a = cardA.dataset[sortKeyDataField];
                    let b = cardB.dataset[sortKeyDataField];
                    if (!isNaN(a) && !isNaN(b)) {
                        return parseInt(a) - parseInt(b);
                    }

                    return a < b ? -1 : a > b ? 1 : 0;
                });

                if (reverse) {
                    sortedCards.reverse();
                }

                parent.innerHTML = "";

                let frag = document.createDocumentFragment();
                sortedCards.forEach(function (card) {
                    frag.appendChild(card);
                });
                parent.appendChild(frag);
            };

            search.addEventListener("input", function () {
                applyFilter();
            });
            applySort();
            applyFilter();

            extraNetworksApplySort[tabname_full] = applySort;
            extraNetworksApplyFilter[tabname_full] = applyFilter;

            let controls = gradioApp().querySelector(
                "#" + tabname_full + "_controls",
            );
            controlsDiv.insertBefore(controls, null);

            if (elem.style.display != "none") {
                extraNetworksShowControlsForPage(tabname, tabname_full);
            }
        });

    registerPrompt(tabname, tabname + "_prompt");
    registerPrompt(tabname, tabname + "_neg_prompt");
}

function extraNetworksMovePromptToTab(
    tabname,
    id,
    showPrompt,
    showNegativePrompt,
) {
    if (!gradioApp().querySelector(".toprow-compact-tools")) return; // only applicable for compact prompt layout

    let promptContainer = gradioApp().getElementById(
        tabname + "_prompt_container",
    );
    let prompt = gradioApp().getElementById(tabname + "_prompt_row");
    let negPrompt = gradioApp().getElementById(tabname + "_neg_prompt_row");
    let elem = id ? gradioApp().getElementById(id) : null;

    if (showNegativePrompt && elem) {
        elem.insertBefore(negPrompt, elem.firstChild);
    } else {
        promptContainer.insertBefore(negPrompt, promptContainer.firstChild);
    }

    if (showPrompt && elem) {
        elem.insertBefore(prompt, elem.firstChild);
    } else {
        promptContainer.insertBefore(prompt, promptContainer.firstChild);
    }

    if (elem) {
        elem.classList.toggle(
            "extra-page-prompts-active",
            showNegativePrompt || showPrompt,
        );
    }
}

function extraNetworksShowControlsForPage(tabname, tabname_full) {
    gradioApp()
        .querySelectorAll(
            "#" + tabname + "_extra_tabs .extra-networks-controls-div > div",
        )
        .forEach(function (elem) {
            let targetId = tabname_full + "_controls";
            elem.style.display = elem.id == targetId ? "" : "none";
        });
}

function extraNetworksUnrelatedTabSelected(tabname) {
    // called from python when user selects an unrelated tab (generate)
    extraNetworksMovePromptToTab(tabname, "", false, false);

    extraNetworksShowControlsForPage(tabname, null);
}

function extraNetworksTabSelected(
    tabname,
    id,
    showPrompt,
    showNegativePrompt,
    tabname_full,
) {
    // called from python when user selects an extra networks tab
    extraNetworksMovePromptToTab(tabname, id, showPrompt, showNegativePrompt);

    extraNetworksShowControlsForPage(tabname, tabname_full);
}

function applyExtraNetworkFilter(tabname_full) {
    let doFilter = function () {
        let applyFunction = extraNetworksApplyFilter[tabname_full];

        if (applyFunction) {
            applyFunction(true);
        }
    };
    setTimeout(doFilter, 1);
}

function applyExtraNetworkSort(tabname_full) {
    let doSort = function () {
        extraNetworksApplySort[tabname_full](true);
    };
    setTimeout(doSort, 1);
}

let extraNetworksApplyFilter = {};
let extraNetworksApplySort = {};
let activePromptTextarea = {};

function setupExtraNetworks() {
    setupExtraNetworksForTab("txt2img");
    setupExtraNetworksForTab("img2img");
}

let re_extranet = /<([^:^>]+:[^:]+):[\d.]+>(.*)/;
let re_extranet_g = /<([^:^>]+:[^:]+):[\d.]+>/g;

let re_extranet_neg = /\(([^:^>]+:[\d.]+)\)/;
let re_extranet_g_neg = /\(([^:^>]+:[\d.]+)\)/g;
function tryToRemoveExtraNetworkFromPrompt(textarea, text, isNeg) {
    let m = text.match(isNeg ? re_extranet_neg : re_extranet);
    let replaced = false;
    let newTextareaText;
    let extraTextBeforeNet = opts.extra_networks_add_text_separator;
    if (m) {
        let extraTextAfterNet = m[2];
        let partToSearch = m[1];
        let foundAtPosition = -1;
        newTextareaText = textarea.value.replaceAll(
            isNeg ? re_extranet_g_neg : re_extranet_g,
            function (found, net, pos) {
                m = found.match(isNeg ? re_extranet_neg : re_extranet);
                if (m[1] == partToSearch) {
                    replaced = true;
                    foundAtPosition = pos;
                    return "";
                }
                return found;
            },
        );
        if (foundAtPosition >= 0) {
            if (
                extraTextAfterNet &&
                newTextareaText.substr(foundAtPosition, extraTextAfterNet.length) ==
                extraTextAfterNet
            ) {
                newTextareaText =
                    newTextareaText.substr(0, foundAtPosition) +
                    newTextareaText.substr(foundAtPosition + extraTextAfterNet.length);
            }
            if (
                newTextareaText.substr(
                    foundAtPosition - extraTextBeforeNet.length,
                    extraTextBeforeNet.length,
                ) == extraTextBeforeNet
            ) {
                newTextareaText =
                    newTextareaText.substr(
                        0,
                        foundAtPosition - extraTextBeforeNet.length,
                    ) + newTextareaText.substr(foundAtPosition);
            }
        }
    } else {
        newTextareaText = textarea.value.replaceAll(
            new RegExp(`((?:${extraTextBeforeNet})?${text})`, "g"),
            "",
        );
        replaced = newTextareaText != textarea.value;
    }

    if (replaced) {
        textarea.value = newTextareaText;
        return true;
    }

    return false;
}

function updatePromptArea(text, textArea, isNeg) {
    if (!tryToRemoveExtraNetworkFromPrompt(textArea, text, isNeg)) {
        textArea.value =
            textArea.value + opts.extra_networks_add_text_separator + text;
    }

    updateInput(textArea);
}

function cardClicked(
    tabname,
    textToAdd,
    textToAddNegative,
    allowNegativePrompt,
) {
    if (textToAddNegative.length > 0) {
        updatePromptArea(
            textToAdd,
            gradioApp().querySelector("#" + tabname + "_prompt > label > textarea"),
        );
        updatePromptArea(
            textToAddNegative,
            gradioApp().querySelector(
                "#" + tabname + "_neg_prompt > label > textarea",
            ),
            true,
        );
    } else {
        let textarea = allowNegativePrompt
            ? activePromptTextarea[tabname]
            : gradioApp().querySelector("#" + tabname + "_prompt > label > textarea");
        updatePromptArea(textToAdd, textarea);
    }
}

function saveCardPreview(event, tabname, filename) {
    let textarea = gradioApp().querySelector(
        "#" + tabname + "_preview_filename  > label > textarea",
    );
    let button = gradioApp().getElementById(tabname + "_save_preview");

    textarea.value = filename;
    updateInput(textarea);

    button.click();

    event.stopPropagation();
    event.preventDefault();
}

function extraNetworksSearchButton(tabname, extra_networks_tabname, event) {
    let searchTextarea = gradioApp().querySelector(
        "#" + tabname + "_" + extra_networks_tabname + "_extra_search",
    );
    let button = event.target;
    let text = button.classList.contains("search-all")
        ? ""
        : button.textContent.trim();

    searchTextarea.value = text;
    updateInput(searchTextarea);
}

function extraNetworksTreeProcessFileClick(
    event,
    btn,
    tabname,
    extra_networks_tabname,
) {
    /**
     * Processes `onclick` events when user clicks on files in tree.
     *
     * @param event                     The generated event.
     * @param btn                       The clicked `tree-list-item` button.
     * @param tabname                   The name of the active tab in the sd webui. Ex: txt2img, img2img, etc.
     * @param extra_networks_tabname    The id of the active extraNetworks tab. Ex: lora, checkpoints, etc.
     */
    // NOTE: Currently unused.
    return;
}

function extraNetworksTreeProcessDirectoryClick(
    event,
    btn,
    tabname,
    extra_networks_tabname,
) {
    /**
     * Processes `onclick` events when user clicks on directories in tree.
     *
     * Here is how the tree reacts to clicks for various states:
     * unselected unopened directory: Directory is selected and expanded.
     * unselected opened directory: Directory is selected.
     * selected opened directory: Directory is collapsed and deselected.
     * chevron is clicked: Directory is expanded or collapsed. Selected state unchanged.
     *
     * @param event                     The generated event.
     * @param btn                       The clicked `tree-list-item` button.
     * @param tabname                   The name of the active tab in the sd webui. Ex: txt2img, img2img, etc.
     * @param extra_networks_tabname    The id of the active extraNetworks tab. Ex: lora, checkpoints, etc.
     */
    let ul = btn.nextElementSibling;
    // This is the actual target that the user clicked on within the target button.
    // We use this to detect if the chevron was clicked.
    let true_targ = event.target;

    function _expand_or_collapse(_ul, _btn) {
        // Expands <ul> if it is collapsed, collapses otherwise. Updates button attributes.
        if (_ul.hasAttribute("hidden")) {
            _ul.removeAttribute("hidden");
            _btn.dataset.expanded = "";
        } else {
            _ul.setAttribute("hidden", "");
            delete _btn.dataset.expanded;
        }
    }

    function _remove_selected_from_all() {
        // Removes the `selected` attribute from all buttons.
        let sels = document.querySelectorAll("div.tree-list-content");
        [...sels].forEach((el) => {
            delete el.dataset.selected;
        });
    }

    function _select_button(_btn) {
        // Removes `data-selected` attribute from all buttons then adds to passed button.
        _remove_selected_from_all();
        _btn.dataset.selected = "";
    }

    function _update_search(_tabname, _extra_networks_tabname, _search_text) {
        // Update search input with select button's path.
        let search_input_elem = gradioApp().querySelector(
            "#" + tabname + "_" + extra_networks_tabname + "_extra_search",
        );
        search_input_elem.value = _search_text;
        updateInput(search_input_elem);
    }

    // If user clicks on the chevron, then we do not select the folder.
    if (
        true_targ.matches(
            ".tree-list-item-action--leading, .tree-list-item-action-chevron",
        )
    ) {
        _expand_or_collapse(ul, btn);
    } else {
        // User clicked anywhere else on the button.
        if ("selected" in btn.dataset && !ul.hasAttribute("hidden")) {
            // If folder is select and open, collapse and deselect button.
            _expand_or_collapse(ul, btn);
            delete btn.dataset.selected;
            _update_search(tabname, extra_networks_tabname, "");
        } else if (!(!("selected" in btn.dataset) && !ul.hasAttribute("hidden"))) {
            // If folder is open and not selected, then we don't collapse; just select.
            // NOTE: Double inversion sucks but it is the clearest way to show the branching here.
            _expand_or_collapse(ul, btn);
            _select_button(btn, tabname, extra_networks_tabname);
            _update_search(tabname, extra_networks_tabname, btn.dataset.path);
        } else {
            // All other cases, just select the button.
            _select_button(btn, tabname, extra_networks_tabname);
            _update_search(tabname, extra_networks_tabname, btn.dataset.path);
        }
    }
}

function extraNetworksTreeOnClick(event, tabname, extra_networks_tabname) {
    /**
     * Handles `onclick` events for buttons within an `extra-network-tree .tree-list--tree`.
     *
     * Determines whether the clicked button in the tree is for a file entry or a directory
     * then calls the appropriate function.
     *
     * @param event                     The generated event.
     * @param tabname                   The name of the active tab in the sd webui. Ex: txt2img, img2img, etc.
     * @param extra_networks_tabname    The id of the active extraNetworks tab. Ex: lora, checkpoints, etc.
     */
    let btn = event.currentTarget;
    let par = btn.parentElement;
    if (par.dataset.treeEntryType === "file") {
        extraNetworksTreeProcessFileClick(
            event,
            btn,
            tabname,
            extra_networks_tabname,
        );
    } else {
        extraNetworksTreeProcessDirectoryClick(
            event,
            btn,
            tabname,
            extra_networks_tabname,
        );
    }
}

function extraNetworksControlSortOnClick(
    event,
    tabname,
    extra_networks_tabname,
) {
    /** Handles `onclick` events for Sort Mode buttons. */

    let self = event.currentTarget;
    let parent = event.currentTarget.parentElement;

    parent.querySelectorAll(".extra-network-control--sort").forEach(function (x) {
        x.classList.remove("extra-network-control--enabled");
    });

    self.classList.add("extra-network-control--enabled");

    applyExtraNetworkSort(tabname + "_" + extra_networks_tabname);
}

function extraNetworksControlSortDirOnClick(
    event,
    tabname,
    extra_networks_tabname,
) {
    /**
     * Handles `onclick` events for the Sort Direction button.
     *
     * Modifies the data attributes of the Sort Direction button to cycle between
     * ascending and descending sort directions.
     *
     * @param event                     The generated event.
     * @param tabname                   The name of the active tab in the sd webui. Ex: txt2img, img2img, etc.
     * @param extra_networks_tabname    The id of the active extraNetworks tab. Ex: lora, checkpoints, etc.
     */
    if (event.currentTarget.dataset.sortdir == "Ascending") {
        event.currentTarget.dataset.sortdir = "Descending";
        event.currentTarget.setAttribute("title", "Sort descending");
    } else {
        event.currentTarget.dataset.sortdir = "Ascending";
        event.currentTarget.setAttribute("title", "Sort ascending");
    }
    applyExtraNetworkSort(tabname + "_" + extra_networks_tabname);
}

function extraNetworksControlTreeViewOnClick(
    event,
    tabname,
    extra_networks_tabname,
) {
    /**
     * Handles `onclick` events for the Tree View button.
     *
     * Toggles the tree view in the extra networks pane.
     *
     * @param event                     The generated event.
     * @param tabname                   The name of the active tab in the sd webui. Ex: txt2img, img2img, etc.
     * @param extra_networks_tabname    The id of the active extraNetworks tab. Ex: lora, checkpoints, etc.
     */
    let button = event.currentTarget;
    button.classList.toggle("extra-network-control--enabled");
    let show = !button.classList.contains("extra-network-control--enabled");

    let pane = gradioApp().getElementById(
        tabname + "_" + extra_networks_tabname + "_pane",
    );
    pane.classList.toggle("extra-network-dirs-hidden", show);
}

function clickLoraRefresh() {
    const targets = [
        "txt2img_lora",
        "txt2img_checkpoints",
        "txt2img_textural_inversion",
        "img2img_lora",
        "img2img_checkpoints",
        "img2img_textural_inversion",
    ];
    targets.forEach(function (t) {
        const tab = gradioApp().getElementById(t + "-button");
        if (tab && tab.getAttribute("aria-selected") == "true") {
            const applyFunction = extraNetworksApplyFilter[t];
            if (applyFunction) {
                applyFunction(true);
            }
        }
    });
}

function extraNetworksControlRefreshOnClick(
    event,
    tabname,
    extra_networks_tabname,
) {
    /**
     * Handles `onclick` events for the Refresh Page button.
     *
     * In order to actually call the python functions in `ui_extra_networks.py`
     * to refresh the page, we created an empty gradio button in that file with an
     * event handler that refreshes the page. So what this function here does
     * is it manually raises a `click` event on that button.
     *
     * @param event                     The generated event.
     * @param tabname                   The name of the active tab in the sd webui. Ex: txt2img, img2img, etc.
     * @param extra_networks_tabname    The id of the active extraNetworks tab. Ex: lora, checkpoints, etc.
     */
    let btn_refresh_internal = gradioApp().getElementById(
        tabname + "_" + extra_networks_tabname + "_extra_refresh_internal",
    );
    btn_refresh_internal.dispatchEvent(new Event("click"));
}

let globalPopup = null;
let globalPopupInner = null;

function closePopup() {
    if (!globalPopup) return;
    globalPopup.style.display = "none";
}

function popup(contents) {
    if (!globalPopup) {
        globalPopup = document.createElement("div");
        globalPopup.classList.add("global-popup");

        let close = document.createElement("div");
        close.classList.add("global-popup-close");
        close.addEventListener("click", closePopup);
        close.title = "Close";
        globalPopup.appendChild(close);

        globalPopupInner = document.createElement("div");
        globalPopupInner.classList.add("global-popup-inner");
        globalPopup.appendChild(globalPopupInner);

        gradioApp().querySelector(".main").appendChild(globalPopup);
    }

    globalPopupInner.innerHTML = "";
    globalPopupInner.appendChild(contents);

    globalPopup.style.display = "flex";
}

let storedPopupIds = {};
function popupId(id) {
    if (!storedPopupIds[id]) {
        storedPopupIds[id] = gradioApp().getElementById(id);
    }

    popup(storedPopupIds[id]);
}

function extraNetworksFlattenMetadata(obj) {
    const result = {};

    // Convert any stringified JSON objects to actual objects
    for (const key of Object.keys(obj)) {
        if (typeof obj[key] === "string") {
            try {
                const parsed = JSON.parse(obj[key]);
                if (parsed && typeof parsed === "object") {
                    obj[key] = parsed;
                }
            } catch (error) {
                continue;
            }
        }
    }

    // Flatten the object
    for (const key of Object.keys(obj)) {
        if (typeof obj[key] === "object" && obj[key] !== null) {
            const nested = extraNetworksFlattenMetadata(obj[key]);
            for (const nestedKey of Object.keys(nested)) {
                result[`${key}/${nestedKey}`] = nested[nestedKey];
            }
        } else {
            result[key] = obj[key];
        }
    }

    // Special case for handling modelspec keys
    for (const key of Object.keys(result)) {
        if (key.startsWith("modelspec.")) {
            result[key.replaceAll(".", "/")] = result[key];
            delete result[key];
        }
    }

    // Add empty keys to designate hierarchy
    for (const key of Object.keys(result)) {
        const parts = key.split("/");
        for (let i = 1; i < parts.length; i++) {
            const parent = parts.slice(0, i).join("/");
            if (!result[parent]) {
                result[parent] = "";
            }
        }
    }

    return result;
}

function extraNetworksShowMetadata(text) {
    try {
        let parsed = JSON.parse(text);
        if (parsed && typeof parsed === "object") {
            parsed = extraNetworksFlattenMetadata(parsed);
            const table = createVisualizationTable(parsed, 0);
            popup(table);
            return;
        }
    } catch (error) {
        console.error(error);
    }

    let elem = document.createElement("pre");
    elem.classList.add("popup-metadata");
    elem.textContent = text;

    popup(elem);
    return;
}

function requestGet(url, data, handler, errorHandler) {
    let xhr = new XMLHttpRequest();
    let args = Object.keys(data)
        .map(function (k) {
            return encodeURIComponent(k) + "=" + encodeURIComponent(data[k]);
        })
        .join("&");
    xhr.open("GET", url + "?" + args, true);

    xhr.onreadystatechange = function () {
        if (xhr.readyState === 4) {
            if (xhr.status === 200) {
                try {
                    let js = JSON.parse(xhr.responseText);
                    handler(js);
                } catch (error) {
                    console.error(error);
                    errorHandler();
                }
            } else {
                errorHandler();
            }
        }
    };
    let js = JSON.stringify(data);
    xhr.send(js);
}

function extraNetworksCopyCardPath(event) {
    navigator.clipboard.writeText(
        event.target.getAttribute("data-clipboard-text"),
    );
    event.stopPropagation();
}

function extraNetworksRequestMetadata(event, extraPage) {
    let showError = function () {
        extraNetworksShowMetadata("there was an error getting metadata");
    };

    let cardName =
        event.target.parentElement.parentElement.getAttribute("data-name");
    if (cardName == null) {
        // from tree
        cardName =
            event.target.parentElement.parentElement.parentElement.getAttribute(
                "data-name",
            );
    }

    requestGet(
        "./sd_extra_networks/metadata",
        { page: extraPage, item: cardName },
        function (data) {
            if (data && data.metadata) {
                extraNetworksShowMetadata(data.metadata);
            } else {
                showError();
            }
        },
        showError,
    );

    event.stopPropagation();
}

let extraPageUserMetadataEditors = {};

function extraNetworksEditUserMetadata(event, tabname, extraPage) {
    let id = tabname + "_" + extraPage + "_edit_user_metadata";

    let editor = extraPageUserMetadataEditors[id];
    if (!editor) {
        editor = {};
        editor.page = gradioApp().getElementById(id);
        editor.nameTextarea = gradioApp().querySelector(
            "#" + id + "_name" + " textarea",
        );
        editor.button = gradioApp().querySelector("#" + id + "_button");
        extraPageUserMetadataEditors[id] = editor;
    }

    let cardName =
        event.target.parentElement.parentElement.getAttribute("data-name");
    if (cardName == null) {
        // from tree
        cardName =
            event.target.parentElement.parentElement.parentElement.getAttribute(
                "data-name",
            );
    }
    editor.nameTextarea.value = cardName;
    updateInput(editor.nameTextarea);

    editor.button.click();

    popup(editor.page);

    event.stopPropagation();
}

function extraNetworksRefreshSingleCard(page, tabname, name) {
    requestGet(
        "./sd_extra_networks/get-single-card",
        { page: page, tabname: tabname, name: name },
        function (data) {
            if (data && data.html) {
                let card = gradioApp().querySelector(
                    `#${tabname}_${page.replace(" ", "_")}_cards > .card[data-name="${name}"]`,
                );

                let newDiv = document.createElement("DIV");
                newDiv.innerHTML = data.html;
                let newCard = newDiv.firstElementChild;

                newCard.style.display = "";
                card.parentElement.insertBefore(newCard, card);
                card.parentElement.removeChild(card);
            }
        },
    );
}


/**
 * Refresh multiple cards without losing filter state
 * @param {string} page - Page name (e.g., "Lora")
 * @param {string} tabname - Tab name (e.g., "txt2img")
 * @param {string} namesJson - JSON array of LoRA names to refresh
 */
function extraNetworksRefreshMultipleCards(page, tabname, namesJson) {
    try {
        const names = JSON.parse(namesJson);
        
        if (!names || names.length === 0) {
            console.log('No cards to refresh');
            return;
        }
        
        console.log(`Refreshing ${names.length} cards:`, names);
        
        // Refresh each card individually
        let refreshed = 0;
        names.forEach(function(name) {
            setTimeout(function() {
                extraNetworksRefreshSingleCard(page, tabname, name);
                refreshed++;
                
                // Re-apply filters after all cards are refreshed
                if (refreshed === names.length) {
                    console.log(`All ${names.length} cards refreshed, re-applying filters...`);
                    setTimeout(function() {
                        applyExtraNetworkFilter(tabname + '_' + page.replace(' ', '_'));
                    }, 500);
                }
            }, refreshed * 100); // Stagger requests by 100ms
        });
        
    } catch (e) {
        console.error('Error refreshing multiple cards:', e);
    }
}


// ============================================================================
// MULTIPLE PREVIEW IMAGES NAVIGATION
// ============================================================================

/**
 * Navigate through multiple preview images on a LORA card
 * @param {Event} event - The click event
 * @param {number} direction - Direction to navigate: -1 for previous, +1 for next
 */
window.navigatePreview = function(event, direction) {
    event.preventDefault();
    event.stopPropagation();
    
    console.log('navigatePreview called with direction:', direction);
    
    // Get the card element
    const button = event.currentTarget;
    const card = button.closest('.card');
    if (!card) {
        console.error('navigatePreview: Could not find card element');
        return;
    }
    
    // Get the image element
    const img = card.querySelector('img.preview');
    if (!img) {
        console.error('navigatePreview: Could not find preview image');
        return;
    }
    
    // Get all preview URLs from data attribute
    let previewUrls = [];
    try {
        const urlsData = img.getAttribute('data-preview-urls');
        if (!urlsData) {
            console.log('navigatePreview: No preview URLs data attribute found');
            return;
        }
        previewUrls = JSON.parse(urlsData);
        console.log('navigatePreview: Found preview URLs:', previewUrls);
    } catch (e) {
        console.error('navigatePreview: Failed to parse preview URLs:', e);
        return;
    }
    
    if (previewUrls.length <= 1) {
        console.log('navigatePreview: Only one preview, nothing to navigate');
        return;
    }
    
    // Get current preview index (stored in data attribute, defaults to 0)
    let currentIndex = parseInt(img.getAttribute('data-current-preview-index') || '0', 10);
    console.log('navigatePreview: Current index:', currentIndex);
    
    // Calculate new index
    currentIndex += direction;
    
    // Wrap around
    if (currentIndex < 0) {
        currentIndex = previewUrls.length - 1;
    } else if (currentIndex >= previewUrls.length) {
        currentIndex = 0;
    }
    
    console.log('navigatePreview: New index:', currentIndex);
    
    // Update image source
    img.src = previewUrls[currentIndex];
    
    // Store new index
    img.setAttribute('data-current-preview-index', currentIndex.toString());
    
    // Update button titles to show current position
    const prevBtn = card.querySelector('.preview-prev-button');
    const nextBtn = card.querySelector('.preview-next-button');
    if (prevBtn && nextBtn) {
        const position = `${currentIndex + 1}/${previewUrls.length}`;
        prevBtn.title = `Previous preview (${position})`;
        nextBtn.title = `Next preview (${position})`;
    }
    
    // Update indicator if it exists
    const indicator = card.querySelector('.preview-indicator');
    if (indicator) {
        indicator.textContent = `${currentIndex + 1}/${previewUrls.length}`;
    }
    
    console.log(`navigatePreview: SUCCESS - Navigated to preview ${currentIndex + 1}/${previewUrls.length}`);
};

/**
 * Add visual indicators for multi-preview cards
 */
function addPreviewIndicators() {
    document.querySelectorAll('.card').forEach(card => {
        const img = card.querySelector('img.preview');
        if (!img) return;
        
        try {
            const urlsData = img.getAttribute('data-preview-urls');
            if (!urlsData) return;
            
            const previewUrls = JSON.parse(urlsData);
            if (previewUrls.length <= 1) return;
            
            // Check if indicator already exists
            if (card.querySelector('.preview-indicator')) return;
            
            // Create indicator
            const indicator = document.createElement('div');
            indicator.className = 'preview-indicator';
            const currentIndex = parseInt(img.getAttribute('data-current-preview-index') || '0', 10);
            indicator.textContent = `${currentIndex + 1}/${previewUrls.length}`;
            
            // Add to card
            card.appendChild(indicator);
            
        } catch (e) {
            console.error('Failed to add preview indicator:', e);
        }
    });
}

/**
 * Setup keyboard navigation for preview images
 */
function setupPreviewKeyboardNavigation() {
    document.addEventListener('keydown', function(event) {
        const hoveredCard = document.querySelector('.card:hover');
        if (!hoveredCard) return;
        
        const img = hoveredCard.querySelector('img.preview');
        if (!img || !img.getAttribute('data-preview-urls')) return;
        
        if (event.key === 'ArrowLeft' || event.key === 'Left') {
            event.preventDefault();
            const prevBtn = hoveredCard.querySelector('.preview-prev-button');
            if (prevBtn) {
                navigatePreview({ 
                    currentTarget: prevBtn,
                    preventDefault: () => {},
                    stopPropagation: () => {}
                }, -1);
            }
        } else if (event.key === 'ArrowRight' || event.key === 'Right') {
            event.preventDefault();
            const nextBtn = hoveredCard.querySelector('.preview-next-button');
            if (nextBtn) {
                navigatePreview({ 
                    currentTarget: nextBtn,
                    preventDefault: () => {},
                    stopPropagation: () => {}
                }, 1);
            }
        }
    });
}

window.addEventListener("keydown", function (event) {
    if (event.key == "Escape") {
        closePopup();
    }
});

/**
 * Setup custom loading for this script.
 * We need to wait for all of our HTML to be generated in the extra networks tabs
 * before we can actually run the `setupExtraNetworks` function.
 * The `onUiLoaded` function actually runs before all of our extra network tabs are
 * finished generating. Thus we needed this new method.
 *
 */

let uiAfterScriptsCallbacks = [];
let uiAfterScriptsTimeout = null;
let executedAfterScripts = false;

function scheduleAfterScriptsCallbacks() {
    clearTimeout(uiAfterScriptsTimeout);
    uiAfterScriptsTimeout = setTimeout(function () {
        executeCallbacks(uiAfterScriptsCallbacks);
    }, 200);
}

onUiLoaded(function () {
    let mutationObserver = new MutationObserver(function (m) {
        let existingSearchfields = gradioApp().querySelectorAll(
            "[id$='_extra_search']",
        ).length;
        let neededSearchfields =
            gradioApp().querySelectorAll("[id$='_extra_tabs'] > .tab-nav > button")
                .length - 2;

        if (!executedAfterScripts && existingSearchfields >= neededSearchfields) {
            mutationObserver.disconnect();
            executedAfterScripts = true;
            scheduleAfterScriptsCallbacks();
            
            // Initialize preview features
            setTimeout(function() {
                addPreviewIndicators();
                setupPreviewKeyboardNavigation();
                console.log('Preview navigation features initialized');
            }, 500);
        }
    });
    mutationObserver.observe(gradioApp(), { childList: true, subtree: true });
});

uiAfterScriptsCallbacks.push(setupExtraNetworks);

/**
 * Toggle pin status for a LORA card
 * @param {string} tabname - Tab name (txt2img/img2img)
 * @param {string} extra_networks_tabname - Extra networks tab name (lora)
 * @param {string} name - LORA name
 * @param {Event} event - Click event
 */
function togglePin(tabname, extra_networks_tabname, name, event) {
    event.preventDefault();
    event.stopPropagation();
    
    console.log('Toggling pin for:', name);
    
    // Send request to backend
    const params = new URLSearchParams({
        page: 'Lora',
        tabname: tabname,
        name: name
    });

    fetch(`/sd_extra_networks/toggle-pin?${params.toString()}`, {
        method: 'GET'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log('Pin toggled successfully:', data.pinned);
            
            // Find the card
            const card = event.target.closest('.card');
            if (!card) {
                console.error('Could not find card element');
                return;
            }
            
            // Update the data attribute
            card.setAttribute('data-sort-pinned', data.pinned ? '1' : '0');
            
            // Update pin badge appearance
            const pinBadge = card.querySelector('.extra-network-pin-badge');
            if (pinBadge) {
                if (data.pinned) {
                    pinBadge.classList.remove('unpinned');
                    pinBadge.title = 'Unpin from top';
                } else {
                    pinBadge.classList.add('unpinned');
                    pinBadge.title = 'Pin to top';
                }
            }
            
            // Re-sort the cards
            const tabname_full = tabname + '_' + extra_networks_tabname;
            if (extraNetworksApplySort[tabname_full]) {
                extraNetworksApplySort[tabname_full](true);
            }

            // Also refresh the single card to update the visual state immediately
            setTimeout(() => {
                extraNetworksRefreshSingleCard('Lora', tabname, data.name);
            }, 100);
            
        } else {
            console.error('Failed to toggle pin:', data.error);
        }
    })
    .catch(error => {
        console.error('Error toggling pin:', error);
    });
}

// Make togglePin available globally
window.togglePin = togglePin;


// ============================================================================
// PREVIEW REORDER DRAG-AND-DROP (Sortable.js Integration)
// ============================================================================

/**
 * Load Sortable.js library from CDN if not already loaded
 */
function loadSortableJS(callback) {
    // Check if Sortable is already loaded
    if (typeof Sortable !== 'undefined') {
        console.log('Sortable.js already loaded');
        callback();
        return;
    }
    
    console.log('Loading Sortable.js from CDN...');
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js';
    script.onload = function() {
        console.log('Sortable.js loaded successfully');
        callback();
    };
    script.onerror = function() {
        console.error('Failed to load Sortable.js from CDN');
    };
    document.head.appendChild(script);
}

/**
 * Initialize drag-and-drop on the preview reorder grid
 */
function initializePreviewReorder() {
    const grid = document.getElementById('preview-reorder-grid');
    if (!grid) {
        console.log('Preview reorder grid not found, skipping initialization');
        return;
    }
    
    // Check if already initialized
    if (grid.sortableInstance) {
        console.log('Preview reorder already initialized');
        return;
    }
    
    console.log('Initializing preview reorder drag-and-drop...');
    
    loadSortableJS(function() {
        try {
            // Initialize Sortable
            const sortable = Sortable.create(grid, {
                animation: 200,
                easing: "cubic-bezier(0.4, 0, 0.2, 1)",
                ghostClass: 'sortable-ghost',
                chosenClass: 'sortable-chosen',
                dragClass: 'sortable-drag',
                forceFallback: false,
                fallbackClass: 'sortable-fallback',
                fallbackOnBody: true,
                swapThreshold: 0.65,
                
                // Handle dragging
                onStart: function(evt) {
                    console.log('Started dragging item', evt.oldIndex);
                },
                
                // Handle drop
                onEnd: function(evt) {
                    console.log('Dropped item from', evt.oldIndex, 'to', evt.newIndex);
                    
                    if (evt.oldIndex === evt.newIndex) {
                        console.log('Item dropped in same position, no change');
                        return;
                    }
                    
                    // Update badges after reorder
                    updateReorderBadges();
                    
                    // Capture new order and save to Gradio state
                    captureReorderState();
                },
            });
            
            // Store instance on grid element
            grid.sortableInstance = sortable;
            
            console.log('Preview reorder initialized successfully');
            
        } catch (error) {
            console.error('Error initializing Sortable:', error);
        }
    });
}

/**
 * Update badges after items are reordered
 */
function updateReorderBadges() {
    const grid = document.getElementById('preview-reorder-grid');
    if (!grid) return;
    
    const items = grid.querySelectorAll('.reorder-item');
    items.forEach((item, index) => {
        const badge = item.querySelector('.reorder-badge');
        if (badge) {
            if (index === 0) {
                badge.textContent = '⭐ MAIN';
                item.setAttribute('data-index', '0');
            } else {
                badge.textContent = `#${index + 1}`;
                item.setAttribute('data-index', index.toString());
            }
        }
    });
    
    console.log('Updated badges for', items.length, 'items');
}

/**
 * Capture current order and save to Gradio hidden state
 */
function captureReorderState() {
    const grid = document.getElementById('preview-reorder-grid');
    if (!grid) {
        console.error('Cannot capture order: grid not found');
        return;
    }
    
    const items = grid.querySelectorAll('.reorder-item');
    const newOrder = [];
    
    items.forEach((item) => {
        // Get original index from data attribute
        const originalIndex = item.getAttribute('data-original-index') || item.getAttribute('data-index');
        newOrder.push(parseInt(originalIndex, 10));
    });
    
    console.log('Captured new order:', newOrder);
    
    // Find the hidden state component by class or ID
    let stateInput = document.querySelector('.reorder-state-input textarea, .reorder-state-input input');
    
    if (!stateInput) {
        // Fallback: search by ID pattern
        stateInput = document.querySelector('[id*="_reorder_state"] textarea, [id*="_reorder_state"] input');
    }
    
    if (!stateInput) {
        console.error('Could not find reorder state input');
        
        // Try alternative selectors
        const allTextareas = document.querySelectorAll('textarea');
        console.log('All textareas:', allTextareas.length);
        
        // Look for state component near the reorder grid
        const container = grid.closest('.edit-user-metadata');
        if (container) {
            const nearbyStates = container.querySelectorAll('textarea[style*="display: none"]');
            console.log('Found', nearbyStates.length, 'hidden textareas near reorder grid');
            
            if (nearbyStates.length > 0) {
                // Use the last hidden textarea (likely the reorder state)
                const stateCandidate = nearbyStates[nearbyStates.length - 1];
                stateCandidate.value = JSON.stringify(newOrder);
                stateCandidate.dispatchEvent(new Event('input', { bubbles: true }));
                console.log('Updated state via fallback method');
                return;
            }
        }
        
        return;
    }
    
    // Update the state
    stateInput.value = JSON.stringify(newOrder);
    stateInput.dispatchEvent(new Event('input', { bubbles: true }));
    
    console.log('Updated Gradio state with new order');
}

/**
 * Capture and return the current order for Gradio
 * This is called when the save button is clicked
 */
function captureAndReturnReorderState() {
    const grid = document.getElementById('preview-reorder-grid');
    
    if (!grid) {
        console.error('Cannot capture order: grid not found');
        return '[]';
    }
    
    const items = grid.querySelectorAll('.reorder-item');
    if (items.length === 0) {
        console.error('No items found in grid');
        return '[]';
    }
    
    const newOrder = [];
    
    items.forEach((item, currentPosition) => {
        // Get original index from data attribute
        const originalIndex = item.getAttribute('data-original-index') || item.getAttribute('data-index');
        const parsedIndex = parseInt(originalIndex, 10);
        
        if (isNaN(parsedIndex)) {
            console.error('Invalid index on item:', item);
        } else {
            newOrder.push(parsedIndex);
        }
        
        console.log(`Position ${currentPosition}: original index ${parsedIndex}`);
    });
    
    const orderJson = JSON.stringify(newOrder);
    console.log('Captured order for save:', orderJson);
    console.log('Order array:', newOrder);
    
    // Also try to update the hidden state input as backup
    updateHiddenStateInput(orderJson);
    
    return orderJson;
}

/**
 * Helper to update hidden state input
 */
function updateHiddenStateInput(orderJson) {
    // Try multiple selectors
    const selectors = [
        '.reorder-state-input textarea',
        '.reorder-state-input input',
        '[id*="_reorder_state"] textarea',
        '[id*="_reorder_state"] input',
        'textarea.reorder-state-input',
        'input.reorder-state-input'
    ];
    
    for (const selector of selectors) {
        const input = document.querySelector(selector);
        if (input) {
            input.value = orderJson;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            console.log('Updated hidden state input via selector:', selector);
            return true;
        }
    }
    
    console.warn('Could not find hidden state input to update');
    return false;
}

/**
 * Store original indices when grid is first loaded
 */
function storeOriginalIndices() {
    const grid = document.getElementById('preview-reorder-grid');
    if (!grid) {
        console.log('Grid not found, cannot store indices');
        return;
    }
    
    const items = grid.querySelectorAll('.reorder-item');
    if (items.length === 0) {
        console.log('No items found in grid');
        return;
    }
    
    items.forEach((item, index) => {
        const currentIndex = item.getAttribute('data-index');
        
        // Always set data-original-index from data-index
        if (!item.hasAttribute('data-original-index') || item.getAttribute('data-original-index') === '') {
            item.setAttribute('data-original-index', currentIndex);
            console.log(`Item ${index}: set original-index to ${currentIndex}`);
        }
    });
    
    console.log('Stored original indices for', items.length, 'items');
    
    // Log all items for debugging
    items.forEach((item, index) => {
        console.log(`Item ${index}: data-index="${item.getAttribute('data-index')}", data-original-index="${item.getAttribute('data-original-index')}"`);
    });
}

/**
 * Setup observer to detect when reorder grid is loaded
 */
function setupReorderGridObserver() {
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            mutation.addedNodes.forEach(function(node) {
                if (node.nodeType === 1) { // Element node
                    // Check if the added node contains the reorder grid
                    if (node.id === 'preview-reorder-grid' || node.querySelector('#preview-reorder-grid')) {
                        console.log('Reorder grid detected in DOM');
                        storeOriginalIndices();
                        setTimeout(initializePreviewReorder, 100);
                    }
                }
            });
        });
    });
    
    // Observe the entire document for changes
    observer.observe(document.body, {
        childList: true,
        subtree: true
    });
    
    console.log('Reorder grid observer initialized');
}

/**
 * Initialize on page load
 */
function initializeReorderFeature() {
    console.log('Initializing preview reorder feature...');
    
    // Try to initialize immediately if grid exists
    storeOriginalIndices();
    initializePreviewReorder();
    
    // Setup observer for future grid loads (when modal opens)
    setupReorderGridObserver();
}

// Initialize when UI is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeReorderFeature);
} else {
    initializeReorderFeature();
}

// Also initialize after extra networks scripts load
uiAfterScriptsCallbacks.push(initializeReorderFeature);

// ============================================================================
// HIERARCHICAL DIRECTORY NAVIGATION
// ============================================================================

/**
 * State management for current directory path
 */
const extraNetworksDirState = {};

/**
 * Initialize directory state for a tab
 */
function initDirState(tabname, extra_networks_tabname) {
    const key = `${tabname}_${extra_networks_tabname}`;
    if (!extraNetworksDirState[key]) {
        extraNetworksDirState[key] = {
            currentPath: "",
            allDirs: new Set()
        };
    }
    return extraNetworksDirState[key];
}

/**
 * Get current directory path for a tab
 */
function getCurrentDirPath(tabname, extra_networks_tabname) {
    const state = initDirState(tabname, extra_networks_tabname);
    return state.currentPath;
}

/**
 * Set current directory path and update UI
 */
function setCurrentDirPath(tabname, extra_networks_tabname, path) {
    const state = initDirState(tabname, extra_networks_tabname);
    state.currentPath = path;
    
    console.log(`[DirNav] Set path for ${tabname}_${extra_networks_tabname}:`, path);
}

/**
 * Navigate to a directory (called when clicking breadcrumb or folder button)
 */
function extraNetworksNavigateDir(tabname, extra_networks_tabname, path, event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    
    console.log(`[DirNav] Navigate to:`, path);
    
    // Normalize path
    path = path.replace(/\\/g, '/').replace(/\/+$/, '');
    if (path) path = path + '/';
    
    // Update state
    setCurrentDirPath(tabname, extra_networks_tabname, path);
    
    // Update UI
    updateDirNavigation(tabname, extra_networks_tabname);
    
    // Filter cards to show only those in current path
    filterCardsByPath(tabname, extra_networks_tabname, path);
}

/**
 * Update breadcrumb and directory buttons based on current path
 */
function updateDirNavigation(tabname, extra_networks_tabname) {
    const currentPath = getCurrentDirPath(tabname, extra_networks_tabname);
    const dirsContainer = document.getElementById(`${tabname}_${extra_networks_tabname}_dirs`);
    
    if (!dirsContainer) {
        console.warn('[DirNav] Directory container not found');
        return;
    }
    
    console.log(`[DirNav] Updating navigation for path:`, currentPath);
    
    // Get all directory buttons
    const allDirButtons = Array.from(dirsContainer.querySelectorAll('.extra-network-subdir-button'));
    
    // Collect all unique directories
    const allDirs = new Set();
    allDirButtons.forEach(btn => {
        const path = btn.getAttribute('data-path');
        if (path) allDirs.add(path);
    });
    
    console.log(`[DirNav] Found ${allDirs.size} total directories`);
    
    // Filter to immediate children only
    const immediateChildren = new Set();
    
    for (const dir of allDirs) {
        // Skip if not under current path
        if (currentPath && !dir.startsWith(currentPath)) {
            continue;
        }
        
        // Get relative path from current path
        let relative = currentPath ? dir.substring(currentPath.length) : dir;
        relative = relative.replace(/^\/+/, '');
        
        // Only include immediate children (no nested subdirectories)
        if (relative.includes('/')) {
            // This is a nested child, extract immediate parent
            const immediateChild = relative.split('/')[0] + '/';
            const fullPath = currentPath + immediateChild;
            immediateChildren.add(fullPath);
        } else if (relative) {
            // This is already an immediate child
            immediateChildren.add(dir);
        }
    }
    
    console.log(`[DirNav] ${immediateChildren.size} immediate children:`, Array.from(immediateChildren));
    
    // Update button visibility
    allDirButtons.forEach(btn => {
        const btnPath = btn.getAttribute('data-path');
        if (immediateChildren.has(btnPath)) {
            btn.style.display = '';
            // Update button text to show only folder name
            const displayName = currentPath ? btnPath.substring(currentPath.length).replace(/\/+$/, '') : btnPath.replace(/\/+$/, '');
            btn.textContent = displayName;
        } else {
            btn.style.display = 'none';
        }
    });
    
    // Update breadcrumb
    updateBreadcrumb(tabname, extra_networks_tabname, currentPath);
}

/**
 * Update breadcrumb navigation
 */
function updateBreadcrumb(tabname, extra_networks_tabname, currentPath) {
    // Try multiple selectors
    let breadcrumbContainer = document.querySelector(`#${tabname}_${extra_networks_tabname}_dirs .extra-network-breadcrumb`);
    
    // If not found, try to find parent dirs container and look inside
    if (!breadcrumbContainer) {
        const dirsContainer = document.getElementById(`${tabname}_${extra_networks_tabname}_dirs`);
        if (dirsContainer) {
            breadcrumbContainer = dirsContainer.querySelector('.extra-network-breadcrumb');
        }
    }
    
    if (!breadcrumbContainer) {
        console.warn('[DirNav] Breadcrumb container not found for', `${tabname}_${extra_networks_tabname}`);
        return;
    }
    
    // Build breadcrumb HTML
    const parts = [];
    
    // Home button
    const homeActive = !currentPath ? ' extra-network-breadcrumb-active' : '';
    parts.push(`
        <button class='extra-network-breadcrumb-item${homeActive}' 
                onclick='extraNetworksNavigateDir("${tabname}", "${extra_networks_tabname}", "", event)'>
            Home
        </button>
    `);
    
    // Path segments
    if (currentPath) {
        const pathParts = currentPath.split('/').filter(p => p);
        let accumulated = '';
        
        pathParts.forEach((part, index) => {
            accumulated += part + '/';
            const isLast = (index === pathParts.length - 1);
            const activeClass = isLast ? ' extra-network-breadcrumb-active' : '';
            
            parts.push(`<span class='extra-network-breadcrumb-separator'>›</span>`);
            parts.push(`
                <button class='extra-network-breadcrumb-item${activeClass}' 
                        onclick='extraNetworksNavigateDir("${tabname}", "${extra_networks_tabname}", "${accumulated}", event)'>
                    ${escapeHtml(part)}
                </button>
            `);
        });
    }
    
    breadcrumbContainer.innerHTML = parts.join('');
    breadcrumbContainer.setAttribute('data-current-path', currentPath);
}

/**
 * Filter cards to show only those in current path (and all subfolders)
 */
function filterCardsByPath(tabname, extra_networks_tabname, path) {
    const cardsContainer = document.getElementById(`${tabname}_${extra_networks_tabname}_cards`);
    
    if (!cardsContainer) {
        console.warn('[DirNav] Cards container not found for', `${tabname}_${extra_networks_tabname}`);
        return;
    }
    
    // Wait a bit if cards aren't loaded yet
    const allCards = cardsContainer.querySelectorAll('.card');
    if (allCards.length === 0) {
        console.log('[DirNav] No cards found yet, waiting...');
        setTimeout(() => filterCardsByPath(tabname, extra_networks_tabname, path), 200);
        return;
    }
    
    console.log(`[DirNav] Filtering ${allCards.length} cards for path:`, path);
    
    let visibleCount = 0;
    
    allCards.forEach(card => {
        // Get card's search terms (which include the path)
        const searchTerms = Array.from(card.querySelectorAll('.search_terms'))
            .map(span => span.textContent.toLowerCase())
            .join(' ');
        
        // Normalize search terms path
        const cardPath = searchTerms.replace(/\\/g, '/');
        
        // Show card if:
        // - We're at root (show all)
        // - Card path starts with current path (in this folder or subfolders)
        const shouldShow = !path || cardPath.includes(path.toLowerCase());
        
        if (shouldShow) {
            card.style.display = '';
            visibleCount++;
        } else {
            card.style.display = 'none';
        }
    });
    
    console.log(`[DirNav] Showing ${visibleCount}/${allCards.length} cards`);
}

/**
 * Helper: Escape HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Initialize directory navigation when page loads
 */
function initializeExtraNetworksDirNav() {
    console.log('[DirNav] Initializing directory navigation');
    
    // Find all extra networks tabs
    const allDirsContainers = document.querySelectorAll('[id$="_dirs"]');
    
    allDirsContainers.forEach(container => {
        const id = container.id;
        const match = id.match(/^(.+)_(.+)_dirs$/);
        
        if (match) {
            const tabname = match[1];
            const extra_networks_tabname = match[2];
            
            console.log(`[DirNav] Found: ${tabname}_${extra_networks_tabname}`);
            
            // Initialize state
            initDirState(tabname, extra_networks_tabname);
            
            // Wait for cards to load before initializing navigation
            waitForCards(tabname, extra_networks_tabname);
        }
    });
}

/**
 * Wait for cards to load, then initialize navigation
 */
function waitForCards(tabname, extra_networks_tabname, attempts = 0) {
    const cardsContainer = document.getElementById(`${tabname}_${extra_networks_tabname}_cards`);
    const cards = cardsContainer ? cardsContainer.querySelectorAll('.card') : [];
    
    if (cards.length > 0 || attempts > 50) {
        // Cards loaded or timeout
        console.log(`[DirNav] Cards loaded for ${tabname}_${extra_networks_tabname}: ${cards.length} cards`);
        
        // Set up initial navigation (root level)
        setTimeout(() => {
            updateDirNavigation(tabname, extra_networks_tabname);
            filterCardsByPath(tabname, extra_networks_tabname, '');
        }, 100);
    } else {
        // Wait and try again
        setTimeout(() => waitForCards(tabname, extra_networks_tabname, attempts + 1), 100);
    }
}

/**
 * Setup observer to re-initialize when extra networks content changes
 */
function setupExtraNetworksObserver() {
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            // Check if cards were added
            mutation.addedNodes.forEach((node) => {
                if (node.nodeType === 1 && node.classList && node.classList.contains('card')) {
                    // A card was added, re-check navigation
                    const cardsContainer = node.closest('[id$="_cards"]');
                    if (cardsContainer) {
                        const match = cardsContainer.id.match(/^(.+)_(.+)_cards$/);
                        if (match) {
                            const tabname = match[1];
                            const extra_networks_tabname = match[2];
                            
                            // Re-apply current filter
                            const currentPath = getCurrentDirPath(tabname, extra_networks_tabname);
                            filterCardsByPath(tabname, extra_networks_tabname, currentPath);
                        }
                    }
                }
            });
        });
    });
    
    // Observe all card containers
    const cardContainers = document.querySelectorAll('[id$="_cards"]');
    cardContainers.forEach(container => {
        observer.observe(container, { childList: true, subtree: true });
    });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initializeExtraNetworksDirNav();
        setupExtraNetworksObserver();
    });
} else {
    initializeExtraNetworksDirNav();
    setupExtraNetworksObserver();
}

// Also initialize after scripts load
if (typeof uiAfterScriptsCallbacks !== 'undefined') {
    uiAfterScriptsCallbacks.push(() => {
        setTimeout(() => {
            initializeExtraNetworksDirNav();
            setupExtraNetworksObserver();
        }, 500);
    });
}

// Hook into existing refresh mechanism
const originalApplyFilter = window.applyExtraNetworkFilter;
if (originalApplyFilter) {
    window.applyExtraNetworkFilter = function(tabname) {
        // Call original function
        const result = originalApplyFilter.apply(this, arguments);
        
        // Re-apply directory navigation after filter
        const match = tabname.match(/^(.+)_(.+)$/);
        if (match) {
            const tab = match[1];
            const type = match[2];
            const currentPath = getCurrentDirPath(tab, type);
            
            setTimeout(() => {
                filterCardsByPath(tab, type, currentPath);
            }, 100);
        }
        
        return result;
    };
}