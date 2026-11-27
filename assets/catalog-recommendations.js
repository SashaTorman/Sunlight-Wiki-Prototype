(function () {
  'use strict';

  // Match the article's subject, never incidental mentions in its full text.
  // Pages opt in explicitly; biographies, books and brand histories do not opt in.
  const termRules = [
    ['aquamarine', /аквамарин/i], ['pearl', /жемчуг|жемчужин/i],
    ['diamonds', /бриллиант/i], ['amber', /янтар/i],
    ['sapphire', /сапфир/i], ['emerald', /изумруд/i], ['ruby', /рубин/i],
    ['topaz', /топаз/i], ['amethyst', /аметист/i], ['agate', /агат/i],
    ['onyx', /оникс/i], ['quartz', /кварц/i], ['turquoise', /бирюз/i],
    ['opal', /опал/i], ['citrine', /цитрин/i], ['peridot', /хризолит/i],
    ['tourmaline', /турмалин/i], ['garnet', /гранат/i],
    ['jade', /нефрит/i],
    ['chains', /плетени|цеп[ьи]|цепоч/i], ['watches', /часы|часов/i],
    ['brooches', /брош|булавк|фибула|аграф/i],
    ['rings', /кольц|колец|колеч|солитер|трилогия/i],
    ['earrings', /серьг|серег|гвоздики|клипсы|жирандоли/i],
    ['bracelets', /браслет/i], ['pendants', /подвеск|кулон|медальон|оберег/i],
    ['necklaces', /колье|ожерел|сотуар|мониста|лавальер|пектораль/i],
    ['gold', /золот/i], ['silver', /серебр/i], ['platinum', /платин/i],
    ['jewelry', /^(авторское ювелирное украшение|ассортимент ювелирных украшений|антикварные изделия|античные изделия|винтажные украшения|ботанические украшения|брендовые изделия|индийские украшения|итальянские украшения|королевские украшения|модульные украшения|траурные украшения|живые украшения|парюра|полупарюра|бижутерия|трансформеры)$/i],
  ];

  function topicForTerm(title) {
    const normalized = title.replace(/[«»]/g, '').trim();
    const match = termRules.find(function (rule) { return rule[1].test(normalized); });
    return match ? match[0] : null;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { topicForTerm: topicForTerm };
    return;
  }

  const script = document.querySelector('script[data-catalog-topic]');
  const article = document.querySelector('article.post');
  if (!script || !article) return;

  const mode = script.dataset.catalogTopic;
  const heading = article.querySelector('h1');
  const base = new URL('.', script.src);
  const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
  let pendingData;
  let generation = 0;
  let block;

  function data() {
    if (!pendingData) {
      pendingData = fetch(new URL('../data/catalog-products.json', base))
        .then(function (response) {
          if (!response.ok) throw new Error('Catalog selections: ' + response.status);
          return response.json();
        }).catch(function (error) {
          pendingData = null;
          throw error;
        });
    }
    return pendingData;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function externalLink(className, text, url) {
    const node = element('a', className, text);
    node.href = url;
    node.target = '_blank';
    node.rel = 'noopener';
    return node;
  }

  function clear() {
    generation += 1;
    if (block) block.remove();
    block = null;
  }

  async function render() {
    clear();
    const ticket = generation;
    const topic = mode === 'term' ? topicForTerm(heading.textContent) : mode;
    if (!topic) return;
    try {
      const catalog = await data();
      if (generation !== ticket) return;
      const group = catalog.groups[topic];
      if (!group || group.products.length !== 5) return;

      block = element('section', 'catalog-picks');
      block.id = 'catalog-picks';
      block.dataset.topic = topic;
      block.setAttribute('aria-labelledby', 'catalog-picks-title');
      const header = element('div', 'catalog-picks-head');
      const title = element('h2', null, 'Подобрали для вас');
      title.id = 'catalog-picks-title';
      header.append(title);
      const list = element('ul', 'catalog-picks-grid plain');
      group.products.forEach(function (product) {
        const item = element('li');
        const link = externalLink('catalog-pick', null, product.url);
        const media = element('span', 'catalog-pick-media');
        const image = element('img');
        image.src = new URL(product.image, base).href;
        image.alt = product.name;
        image.width = 400;
        image.height = 400;
        image.loading = 'lazy';
        image.decoding = 'async';
        media.append(image);
        link.append(media, element('span', 'catalog-pick-brand', product.brand),
          element('span', 'catalog-pick-name', product.name),
          element('span', 'catalog-pick-price', money.format(product.price)));
        item.append(link);
        list.append(item);
      });
      const more = externalLink('catalog-picks-more', 'Смотреть ещё', group.url);
      more.setAttribute('aria-label', 'Смотреть ещё: ' + group.label + ' в каталоге SUNLIGHT');
      header.append(more);
      block.append(header, list);
      // After the article, before the editorial "Читайте дальше" section.
      article.after(block);
    } catch (error) {
      // An unavailable data file must not break the article or show unrelated goods.
      console.warn('Не удалось загрузить подборку SUNLIGHT:', error.message);
    }
  }

  if (mode === 'term') {
    // Dictionary content loads asynchronously and is reused for every hash route.
    new MutationObserver(render).observe(heading, { childList: true, characterData: true, subtree: true });
    addEventListener('hashchange', clear);
  }
  render();
})();
