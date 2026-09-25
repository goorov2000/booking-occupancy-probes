/* excerpt of https://sutochno.ru/doc/js/asset.index.js?8caf3b18, snapshot 2026-09-08: the legacy transport of listing pages declares the anonymous app token as wp.settings.apiToken and sends it as form field token (wp.requestHelper.setSystemValues) */
tDataTargetLink()});

/**
 * FILE:[default/api-min.js]
 */

wp.settings={apiToken:"SutochnoAppKeyPlaceholder0000000"},wp.isEmpty=function(a){return void 0===a||$.isArray(a)&&0==a.length||$.isPlainObject(a)&&$.isEmptyObject(a)},wp.request=function(){var a=new wp.requestHelper;return a.setParams(arguments),a.send()},wp.requestHelper=function(){this.params={url:null,data:{},callback:{}}},wp.requestHelper.prototype.setSystemValues=function(){this.params.data.platform="js",this.params.d
