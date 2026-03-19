frappe.provide("vitalvida");

vitalvida.THEMES = [
    {name: "Firestorm", primary: "#FF4500", sidebar: "#2d1000", navbar: "#1a0a00"},
    {name: "Naija Power", primary: "#008751", sidebar: "#002d17", navbar: "#003d1f"},
    {name: "Revenue Blue", primary: "#0066CC", sidebar: "#001228", navbar: "#001833"},
    {name: "Growth Machine", primary: "#00C851", sidebar: "#001208", navbar: "#001a0d"},
    {name: "Market Rush", primary: "#FF6600", sidebar: "#0d0800", navbar: "#1a0d00"},
    {name: "Power Magenta", primary: "#CC0066", sidebar: "#0d000a", navbar: "#1a0011"},
    {name: "Cobalt Edge", primary: "#0047AB", sidebar: "#000810", navbar: "#000d1a"},
    {name: "Teal Command", primary: "#008080", sidebar: "#001010", navbar: "#001a1a"},
];

vitalvida.applyTheme = function(theme) {
    var style = document.getElementById("sf-theme-style");
    if (!style) {
        style = document.createElement("style");
        style.id = "sf-theme-style";
        document.head.appendChild(style);
    }
    style.innerHTML = [
        ".navbar { background-color: " + theme.navbar + " !important; }",
        ".desk-sidebar { background-color: " + theme.sidebar + " !important; }",
        ".btn-primary { background-color: " + theme.primary + " !important; border-color: " + theme.primary + " !important; }",
        ".sidebar-item.selected { background-color: " + theme.primary + "33 !important; color: " + theme.primary + " !important; }",
        ".widget-head .widget-title { color: " + theme.primary + " !important; }",
    ].join("\n");
    localStorage.setItem("sf_theme", JSON.stringify(theme));
};

vitalvida.addThemePicker = function() {
    if (document.getElementById("sf-theme-picker")) return;

    var saved = localStorage.getItem("sf_theme");
    if (saved) {
        try { vitalvida.applyTheme(JSON.parse(saved)); } catch(e) {}
    }

    var picker = document.createElement("div");
    picker.id = "sf-theme-picker";
    picker.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:99999;";

    var btn = document.createElement("button");
    btn.innerHTML = "🎨 Theme";
    btn.style.cssText = "background:#333;color:white;border:none;padding:8px 14px;border-radius:20px;cursor:pointer;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,0.3);";

    var dropdown = document.createElement("div");
    dropdown.style.cssText = "display:none;position:absolute;bottom:44px;right:0;background:#1a1a2e;border-radius:12px;padding:10px;min-width:200px;box-shadow:0 4px 20px rgba(0,0,0,0.5);";

    vitalvida.THEMES.forEach(function(theme) {
        var item = document.createElement("div");
        item.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;border-radius:8px;color:white;font-size:13px;";
        item.innerHTML = '<span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:' + theme.primary + ';flex-shrink:0;"></span>' + theme.name;
        item.onmouseover = function() { item.style.background = "rgba(255,255,255,0.1)"; };
        item.onmouseout = function() { item.style.background = "transparent"; };
        item.onclick = function() {
            vitalvida.applyTheme(theme);
            dropdown.style.display = "none";
            btn.innerHTML = "🎨 " + theme.name;
        };
        dropdown.appendChild(item);
    });

    btn.onclick = function() {
        dropdown.style.display = dropdown.style.display === "none" ? "block" : "none";
    };

    picker.appendChild(dropdown);
    picker.appendChild(btn);
    document.body.appendChild(picker);
};

$(document).ready(function() {
    setTimeout(vitalvida.addThemePicker, 2000);
});

$(document).on("page-change", function() {
    setTimeout(vitalvida.addThemePicker, 500);
});
