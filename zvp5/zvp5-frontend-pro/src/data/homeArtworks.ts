import selfPortrait from '@/assets/home-artworks/self-portrait-with-straw-hat.jpg'
import irises from '@/assets/home-artworks/irises.jpg'
import wheatField from '@/assets/home-artworks/wheat-field-with-cypresses.jpg'

export interface HomeArtwork {
  id: string
  title: string
  artist: string
  year: string
  sourceUrl: string
  license: string
  src: string
  width: number
  height: number
}

// The v1 assignment hash depends on this order and on every artwork id.
// Add or reorder entries only with an explicit assignment migration.
export const HOME_ARTWORKS: readonly HomeArtwork[] = [
  {
    id: 'met-436532',
    title: '戴草帽的自画像',
    artist: '文森特·梵高',
    year: '1887',
    sourceUrl:
      'https://commons.wikimedia.org/wiki/File:Self-Portrait_with_a_Straw_Hat_(obverse-_The_Potato_Peeler)_MET_DT1502_cropped2.jpg',
    license: 'CC0 1.0 (Met Open Access)',
    src: selfPortrait,
    width: 1029,
    height: 1280
  },
  {
    id: 'met-436528',
    title: '鸢尾花',
    artist: '文森特·梵高',
    year: '1890',
    sourceUrl: 'https://commons.wikimedia.org/wiki/File:Irises_MET_DP346474.jpg',
    license: 'CC0 1.0 (Met Open Access)',
    src: irises,
    width: 1280,
    height: 1015
  },
  {
    id: 'met-436535',
    title: '麦田与柏树',
    artist: '文森特·梵高',
    year: '1889',
    sourceUrl: 'https://commons.wikimedia.org/wiki/File:Wheat_Field_with_Cypresses_MET_DT1567.jpg',
    license: 'CC0 1.0 (Met Open Access)',
    src: wheatField,
    width: 1280,
    height: 1013
  }
]
