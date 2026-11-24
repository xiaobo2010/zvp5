// Manual verification of rotation matrix computation
// Copy computeScreenOrientedAngles function and test it

function computeScreenOrientedAngles(
  alpha: number,
  beta: number,
  gamma: number,
  screenAngle: number
): { pitch: number; roll: number } {
  const a = (alpha * Math.PI) / 180
  const b = (beta * Math.PI) / 180
  const g = (gamma * Math.PI) / 180

  const ca = Math.cos(a), sa = Math.sin(a)
  const cb = Math.cos(b), sb = Math.sin(b)
  const cg = Math.cos(g), sg = Math.sin(g)

  const r11 = ca * cg - sa * sb * sg
  const r12 = -sa * cb
  const r13 = ca * sg + sa * sb * cg
  const r21 = sa * cg + ca * sb * sg
  const r22 = ca * cb
  const r23 = sa * sg - ca * sb * cg
  const r31 = -cb * sg
  const r32 = sb
  const r33 = cb * cg

  const theta = (screenAngle * Math.PI) / 180
  const c = Math.cos(theta), s = Math.sin(theta)
  
  const vrWorldX = r11 * c + r12 * s
  const vrWorldY = r21 * c + r22 * s
  const vrWorldZ = r31 * c + r32 * s
  
  const vuWorldX = r11 * (-s) + r12 * c
  const vuWorldY = r21 * (-s) + r22 * c
  const vuWorldZ = r31 * (-s) + r32 * c

  const pitch = Math.atan2(vuWorldZ, Math.sqrt(vuWorldX * vuWorldX + vuWorldY * vuWorldY))
  const roll = Math.atan2(vrWorldZ, Math.sqrt(vrWorldX * vrWorldX + vrWorldY * vrWorldY))

  return {
    pitch: (pitch * 180) / Math.PI,
    roll: (roll * 180) / Math.PI
  }
}

// Test cases
const testCases = [
  {
    desc: 'Case 1: Horizontal device, portrait (baseline)',
    input: { alpha: 0, beta: 0, gamma: 0, screenAngle: 0 },
    expected: 'pitch ≈ 0°, roll ≈ 0°'
  },
  {
    desc: 'Case 2: Upright portrait device (β=90)',
    input: { alpha: 0, beta: 90, gamma: 0, screenAngle: 0 },
    expected: 'pitch ≈ 90°, roll ≈ 0° (screen vertical, facing user)'
  },
  {
    desc: 'Case 3: Horizontal device, landscape',
    input: { alpha: 0, beta: 0, gamma: 0, screenAngle: 90 },
    expected: 'pitch ≈ 0°, roll ≈ 0°'
  },
  {
    desc: 'Case 4: Forward tilt 30° in portrait',
    input: { alpha: 0, beta: 30, gamma: 0, screenAngle: 0 },
    expected: 'pitch ≈ 30°, roll ≈ 0°'
  },
  {
    desc: 'Case 5: Gamma=20 in landscape → forward tilt (pitch)',
    input: { alpha: 0, beta: 0, gamma: 20, screenAngle: 90 },
    expected: 'pitch ≈ 20°, roll ≈ 0° (device rotates around visual-right axis)'
  },
  {
    desc: 'Case 6: Beta=20 in landscape → lateral tilt (roll)',
    input: { alpha: 0, beta: 20, gamma: 0, screenAngle: 90 },
    expected: 'pitch ≈ 0°, roll ≈ 20° (device rotates around visual-up axis)'
  },
  {
    desc: 'Case 7: Upside-down in portrait (β=180)',
    input: { alpha: 0, beta: 180, gamma: 0, screenAngle: 0 },
    expected: 'pitch ≈ -180°, roll ≈ ±180° (device upside down, screen facing away)'
  },
  {
    desc: 'Case 8: 90° combinations in landscape',
    input: { alpha: 0, beta: 45, gamma: 45, screenAngle: 90 },
    expected: 'pitch/roll mixed (combined tilt)'
  }
]

console.log('=== Rotation Matrix Verification ===\n')

testCases.forEach(({ desc, input, expected }) => {
  const result = computeScreenOrientedAngles(
    input.alpha,
    input.beta,
    input.gamma,
    input.screenAngle
  )
  
  console.log(`${desc}`)
  console.log(`  Input: α=${input.alpha}° β=${input.beta}° γ=${input.gamma}° screenAngle=${input.screenAngle}°`)
  console.log(`  Output: pitch=${result.pitch.toFixed(2)}° roll=${result.roll.toFixed(2)}°`)
  console.log(`  Expected: ${expected}`)
  console.log()
})
