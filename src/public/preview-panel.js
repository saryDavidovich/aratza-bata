(function () {
  var col = document.querySelector('.preview-col');
  if (!col) return;

  var listId = col.getAttribute('data-list-id');
  var outlineRoot = document.getElementById('pp-outline-root');
  var iframe = document.getElementById('pp-iframe');

  function refreshIframe() {
    if (iframe) iframe.src = '/admin/lists/' + listId + '/preview?_=' + Date.now();
  }

  function loadOutline() {
    fetch('/admin/lists/' + listId + '/preview-panel')
      .then(function (r) { return r.text(); })
      .then(function (html) {
        outlineRoot.innerHTML = html;
        wireDragAndDrop();
      })
      .catch(function () {
        outlineRoot.innerHTML = '<div class="pp-empty">שגיאה בטעינת התצוגה המקדימה.</div>';
      });
  }

  var draggedEl = null;

  function wireDragAndDrop() {
    var list = document.getElementById('pp-list');
    if (!list) return;

    var items = list.querySelectorAll('.pp-item');
    items.forEach(function (item) {
      item.addEventListener('dragstart', function () {
        draggedEl = item;
        item.classList.add('pp-dragging');
      });
      item.addEventListener('dragend', function () {
        item.classList.remove('pp-dragging');
        draggedEl = null;
        saveOrder();
      });
      item.addEventListener('dragover', function (e) {
        e.preventDefault();
        if (!draggedEl || draggedEl === item) return;
        var rect = item.getBoundingClientRect();
        var midpoint = rect.top + rect.height / 2;
        if (e.clientY < midpoint) {
          list.insertBefore(draggedEl, item);
        } else {
          list.insertBefore(draggedEl, item.nextSibling);
        }
      });
    });
  }

  function saveOrder() {
    var list = document.getElementById('pp-list');
    if (!list) return;
    var order = Array.prototype.map.call(list.querySelectorAll('.pp-item'), function (el) {
      return Number(el.getAttribute('data-id'));
    });
    if (order.length === 0) return;

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    fetch('/admin/lists/' + listId + '/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: order, _csrf: csrfMeta ? csrfMeta.content : '' })
    })
      .then(function () { refreshIframe(); })
      .catch(function () { /* אם השמירה נכשלה, הגרירה בעמוד לא תישמר בטעינה הבאה */ });
  }

  document.addEventListener('DOMContentLoaded', loadOutline);
  if (document.readyState !== 'loading') loadOutline();
})();
