// FAS 5 i18n extensions — merged on top of FAS 4 I18N object.
const I18N_FAS5 = {
  sv: {
    nav: { trash:'Papperskorg', rules:'Scan-regler', patterns:'AI-inlärning', cards:'Kortmatchning' },
  },
  en: {
    nav: { trash:'Trash', rules:'Scan rules', patterns:'AI learning', cards:'Card matching' },
  }
};

// Merge into existing I18N (defined in i18n.jsx) once both are loaded
if (window.I18N) {
  Object.assign(window.I18N.sv.nav, I18N_FAS5.sv.nav);
  Object.assign(window.I18N.en.nav, I18N_FAS5.en.nav);
}
