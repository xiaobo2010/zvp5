describe('public application shell', () => {
  it('serves the login page', () => {
    cy.visit('/user/login')
    cy.location('pathname').should('eq', '/user/login')
    cy.get('body').should('be.visible')
  })
})
