(function () {
  "use strict";

  var refreshButton = document.getElementById("lineupRefresh");
  var refreshProgress = document.getElementById("lineupRefreshProgress");
  var csrf = document.querySelector('.lh-sync [name="csrfmiddlewaretoken"]');

  function showRefreshProgress(label) {
    refreshButton.disabled = true;
    refreshButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Actualizando…';
    refreshProgress.hidden = false;
    refreshProgress.textContent = label || "En cola…";
  }

  function pollRefresh(jobId) {
    fetch("/alineaciones/estado/", { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var job = (data.jobs || []).find(function (item) { return item.id === jobId; });
        if (!job) {
          window.setTimeout(function () { pollRefresh(jobId); }, 1200);
          return;
        }
        showRefreshProgress(job.progress_label || "Actualizando…");
        if (job.status === "queued" || job.status === "running") {
          window.setTimeout(function () { pollRefresh(jobId); }, 1200);
          return;
        }
        if (job.status === "succeeded") {
          refreshProgress.textContent = (job.card_count || 0) + " cartas actualizadas. Recargando…";
          window.setTimeout(function () { window.location.reload(); }, 350);
          return;
        }
        refreshButton.disabled = false;
        refreshButton.innerHTML = '<i class="fas fa-arrows-rotate"></i> Reintentar';
        refreshProgress.textContent = job.error || "No se pudo actualizar";
      })
      .catch(function () { window.setTimeout(function () { pollRefresh(jobId); }, 2500); });
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", function () {
      showRefreshProgress("En cola para consultar tus cartas");
      fetch("/alineaciones/actualizar/", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf && csrf.value },
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (!data.job_id) throw new Error(data.error || "No se pudo iniciar");
          pollRefresh(data.job_id);
        })
        .catch(function (error) {
          refreshButton.disabled = false;
          refreshButton.innerHTML = '<i class="fas fa-arrows-rotate"></i> Reintentar';
          refreshProgress.hidden = false;
          refreshProgress.textContent = error.message || "No se pudo iniciar";
        });
    });
  }

  var dataNode = document.getElementById("lineupCardsData");
  if (!dataNode) return;

  var cards = JSON.parse(dataNode.textContent || "[]");
  var positions = ["GK", "DEF", "MID", "FWD", "CLASSIC"];
  var chosen = {};
  // Combinaciones: grupos de candidatas que deben ir en la misma alineación.
  var combos = [];
  var linking = null;
  var savedCombos = {};
  cards.forEach(function (card) {
    if (card.candidate) chosen[String(card.asset_id)] = card;
    if (card.candidate && card.combo) {
      (savedCombos[card.combo] = savedCombos[card.combo] || []).push(String(card.asset_id));
    }
  });
  Object.keys(savedCombos).forEach(function (key) {
    if (savedCombos[key].length > 1) combos.push(savedCombos[key]);
  });
  // El campo oculto y la barra de combinaciones se crean aquí.
  var combosInput = document.createElement("input");
  combosInput.type = "hidden";
  combosInput.name = "combos";
  document.getElementById("candidateAssetIds").after(combosInput);
  var comboBar = document.createElement("div");
  comboBar.className = "lh-combos";
  var comboHelp = document.createElement("span");
  comboHelp.textContent = "Juntos: pulsa la cadena de una candidata y luego la de otra para que vayan en la misma alineación.";
  var comboList = document.createElement("div");
  comboList.id = "comboList";
  comboBar.append(comboHelp, comboList);
  document.querySelector(".lh-lanes").before(comboBar);

  var selectedInput = document.getElementById("candidateAssetIds");
  var selectedTotal = document.getElementById("selectedTotal");
  var starterFilter = document.getElementById("starterFilter");
  var starterNote = document.getElementById("starterFilterNote");
  var hasStarterData = cards.some(function (card) { return card.starter_percent != null; });
  // Sorare no siempre publica la titularidad; sin datos el filtro dejaría el
  // buscador vacío.
  if (starterFilter && !hasStarterData) {
    starterFilter.disabled = true;
    if (starterNote) starterNote.hidden = false;
  }
  // Se recuerda el último filtro salvo que la página ya venga con uno elegido.
  if (starterFilter && !starterFilter.disabled && starterFilter.value === "0") {
    try {
      var savedFilter = localStorage.getItem("lineupStarterFilter");
      if (savedFilter && starterFilter.querySelector('option[value="' + savedFilter + '"]')) starterFilter.value = savedFilter;
    } catch (error) {}
  }

  function minimumStarter() {
    return starterFilter && !starterFilter.disabled ? Number(starterFilter.value) || 0 : 0;
  }

  // Por defecto, las cartas con mejor media primero.
  function byAverage(a, b) {
    return (Number(b.average) || 0) - (Number(a.average) || 0);
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char];
    });
  }

  function photo(url) {
    return url ? '<img src="' + escapeHtml(url) + '" alt="">' : '<i class="fas fa-user"></i>';
  }

  function average(card) {
    return card.average == null ? "0" : escapeHtml(card.average);
  }

  function starter(card) {
    if (card.starter_percent == null) return "";
    var value = Number(card.starter_percent);
    var level = value >= 70 ? "high" : value >= 40 ? "mid" : "low";
    var title = "Probabilidad de titularidad según Sorare";
    if (card.starter_reliability) title += " · fiabilidad " + String(card.starter_reliability).toLowerCase();
    return '<b class="starter starter-' + level + '" title="' + escapeHtml(title) + '">' + escapeHtml(value) + "%</b>";
  }

  // El filtro es deliberadamente tolerante: escribir "alvaro" o "vinicius"
  // encuentra igualmente Álvaro García y Vinícius, sin exigir tildes.
  function searchNormalize(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("es");
  }

  function fixture(card) {
    if (!card.fixture_home_code || !card.fixture_away_code) {
      return '<small>' + escapeHtml(card.team) + '</small>';
    }
    var home = escapeHtml(card.fixture_home_code);
    var away = escapeHtml(card.fixture_away_code);
    if (card.fixture_is_home) home = "<b>" + home + "</b>";
    else away = "<b>" + away + "</b>";
    return '<small class="fixture">' + home + ' <i>-</i> ' + away + '</small>';
  }

  function comboIndex(id) {
    for (var i = 0; i < combos.length; i += 1) {
      if (combos[i].indexOf(id) !== -1) return i;
    }
    return -1;
  }

  function comboLetter(index) {
    return String.fromCharCode(65 + (index % 26));
  }

  function comboTag(index) {
    var tag = document.createElement("em");
    tag.className = "combo-tag";
    tag.textContent = comboLetter(index);
    return tag;
  }

  function iconButton(icon, title) {
    var button = document.createElement("button");
    button.type = "button";
    button.title = title;
    var glyph = document.createElement("i");
    glyph.className = "fas " + icon;
    button.appendChild(glyph);
    return button;
  }

  function drawCombos() {
    comboList.replaceChildren();
    combos.forEach(function (group, index) {
      var chip = document.createElement("span");
      chip.className = "combo-chip";
      var remove = iconButton("fa-xmark", "Quitar combinación");
      remove.onclick = function () {
        combos.splice(index, 1);
        syncSelected();
        positions.forEach(drawSelected);
      };
      chip.append(comboTag(index), " " + group.map(function (id) {
        return chosen[id] ? chosen[id].player : id;
      }).join(" + ") + " ", remove);
      comboList.appendChild(chip);
    });
  }

  function link(id) {
    if (linking === null) {
      linking = id;
    } else if (linking === id) {
      linking = null;
    } else {
      var first = comboIndex(linking);
      var second = comboIndex(id);
      if (first === -1 && second === -1) {
        combos.push([linking, id]);
      } else if (second === -1) {
        combos[first].push(id);
      } else if (first === -1) {
        combos[second].push(linking);
      } else if (first !== second) {
        combos[first] = combos[first].concat(combos[second]);
        combos.splice(second, 1);
      }
      linking = null;
    }
    syncSelected();
    positions.forEach(drawSelected);
  }

  function unlink(id) {
    var index = comboIndex(id);
    if (index === -1) return;
    combos[index] = combos[index].filter(function (member) { return member !== id; });
    if (combos[index].length < 2) combos.splice(index, 1);
  }

  function syncSelected() {
    // Una carta que deja de ser candidata sale también de su combinación.
    combos.slice().forEach(function (group) {
      group.forEach(function (id) { if (!chosen[id]) unlink(id); });
    });
    if (linking !== null && !chosen[linking]) linking = null;
    selectedInput.value = Object.keys(chosen).join(",");
    combosInput.value = combos.map(function (group) { return group.join("+"); }).join(";");
    if (selectedTotal) selectedTotal.textContent = Object.keys(chosen).length;
    drawCombos();
  }

  function selectedAt(position) {
    return Object.keys(chosen).map(function (id) { return chosen[id]; }).filter(function (card) {
      return position === "CLASSIC" ? !card.is_in_season : card.is_in_season && card.position === position;
    }).sort(byAverage);
  }

  function drawSelected(position) {
    var box = document.getElementById("lane-" + position);
    var cardsAtPosition = selectedAt(position);
    document.getElementById("count-" + position).textContent = cardsAtPosition.length;
    box.innerHTML = cardsAtPosition.length ? "" : '<span class="empty">Sin candidatas</span>';
    cardsAtPosition.forEach(function (card) {
      var row = document.createElement("article");
      var minimum = minimumStarter();
      // Las candidatas por debajo del filtro se atenúan: no entran en la propuesta.
      row.className = "candidate" + (minimum && (card.starter_percent == null || Number(card.starter_percent) < minimum) ? " below" : "");
      row.innerHTML = '<span class="candidate-photo">' + photo(card.player_picture_url) + '</span><span><strong>' + escapeHtml(card.player) + "</strong>" + fixture(card) + '</span>' + starter(card) + '<b class="average">' + average(card) + '</b><button type="button"><i class="fas fa-xmark"></i></button>';
      row.querySelector("button").onclick = function () {
        delete chosen[String(card.asset_id)];
        syncSelected();
        drawSelected(position);
        drawResults(position);
      };
      var id = String(card.asset_id);
      var combo = comboIndex(id);
      if (linking === id) row.classList.add("linking");
      if (combo !== -1) row.querySelector("strong").appendChild(comboTag(combo));
      var linkButton = iconButton("fa-link", linking === null ? "Juntar con otra candidata" : linking === id ? "Cancelar" : "Juntar con la candidata marcada");
      linkButton.classList.add("link");
      if (combo !== -1) linkButton.classList.add("on");
      linkButton.onclick = function () { link(id); };
      row.lastElementChild.before(linkButton);
      box.appendChild(row);
    });
  }

  function drawResults(position) {
    var input = document.querySelector('.lane-input[data-position="' + position + '"]');
    var box = document.getElementById("results-" + position);
    var term = searchNormalize(input.value).trim();
    var minimum = minimumStarter();
    var matches = cards.filter(function (card) {
      if (position === "CLASSIC") {
        if (card.is_in_season) return false;
      } else if (!card.is_in_season || card.position !== position) {
        return false;
      }
      if (minimum && (card.starter_percent == null || Number(card.starter_percent) < minimum)) return false;
      var text = searchNormalize(String(card.player || "") + " " + String(card.team || ""));
      return !term || text.includes(term);
    }).sort(byAverage).slice(0, 14);
    box.innerHTML = "";
    if (!matches.length) {
      box.innerHTML = '<div class="empty">' + (term || minimumStarter() ? "No hay coincidencias" : "No hay cartas guardadas para esta posición") + "</div>";
      return;
    }
    matches.forEach(function (card) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "lane-result";
      row.disabled = Boolean(chosen[String(card.asset_id)]);
      row.innerHTML = '<span class="lane-photo">' + photo(card.player_picture_url) + '</span><span><strong>' + escapeHtml(card.player) + "</strong>" + fixture(card) + '</span>' + starter(card) + '<b class="average">' + average(card) + "</b>";
      row.onclick = function () {
        chosen[String(card.asset_id)] = card;
        syncSelected();
        drawSelected(position);
        drawResults(position);
      };
      box.appendChild(row);
    });
  }

  if (starterFilter) {
    starterFilter.addEventListener("change", function () {
      try { localStorage.setItem("lineupStarterFilter", starterFilter.value); } catch (error) {}
      positions.forEach(function (position) {
        drawSelected(position);
        var input = document.querySelector('.lane-input[data-position="' + position + '"]');
        if (input.value || document.getElementById("results-" + position).innerHTML) drawResults(position);
      });
    });
  }
  positions.forEach(function (position) {
    drawSelected(position);
    var input = document.querySelector('.lane-input[data-position="' + position + '"]');
    input.addEventListener("focus", function () { drawResults(position); });
    input.addEventListener("input", function () { drawResults(position); });
  });
  document.addEventListener("pointerdown", function (event) {
    positions.forEach(function (position) {
      var lane = document.getElementById("results-" + position).closest(".lh-lane");
      if (!lane.contains(event.target)) document.getElementById("results-" + position).innerHTML = "";
    });
  });
  var clearButton = document.getElementById("clearCandidates");
  if (clearButton) {
    clearButton.addEventListener("click", function () {
      chosen = {};
      syncSelected();
      positions.forEach(drawSelected);
      // Limpiar también se guarda; si no, al recargar o actualizar volvían
      // las candidatas.
      var form = clearButton.closest("form");
      var action = document.createElement("input");
      action.type = "hidden";
      action.name = "action";
      action.value = "clear";
      form.appendChild(action);
      clearButton.disabled = true;
      form.submit();
    });
  }
  syncSelected();
}());
