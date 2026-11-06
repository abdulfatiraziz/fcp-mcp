// Render centred white text on a transparent PNG: caption.js <text> <out.png> <w> <h> <pt> [x y]
ObjC.import('AppKit');
function run(argv) {
  const text = argv[0], out = argv[1], w = +argv[2], h = +argv[3], pt = +argv[4];
  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
    null, w, h, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext($.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep));
  const font = $.NSFont.fontWithNameSize('HelveticaNeue-Bold', pt);
  const attrs = $.NSMutableDictionary.alloc.init;
  attrs.setObjectForKey(font, $.NSFontAttributeName);
  attrs.setObjectForKey($.NSColor.whiteColor, $.NSForegroundColorAttributeName);
  const s = $.NSString.alloc.initWithUTF8String(text);
  const size = s.sizeWithAttributes(attrs);
  const x = argv.length > 5 ? +argv[5] : (w - size.width) / 2;
  const y = argv.length > 6 ? h - +argv[6] - size.height : (h - size.height) / 2;
  s.drawAtPointWithAttributes($.NSMakePoint(x, y), attrs);
  $.NSGraphicsContext.restoreGraphicsState;
  rep.representationUsingTypeProperties(4, $()).writeToFileAtomically(out, true);  // 4 = PNG
}
